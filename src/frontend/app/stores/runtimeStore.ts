import { defineStore } from 'pinia'
import type {
  ApprovalRequest,
  AttachmentRef,
  McpConnection,
  McpPackage,
  RunStatus,
  SessionRecord,
  RuntimeEvent,
  RuntimeInfo,
  TaskNode,
} from '~/types/agent'
import { isTerminalStatus, isTerminalTaskStatus, normalizeRun } from '~/utils/normalizers'
import {
  connectRuntime,
  onRuntimeEvent,
  restartRuntimeBroker,
  runtimeRequest,
} from '~/utils/runtimeClient'
import { useSessionStore } from '~/stores/sessionStore'
import { useProjectStore } from '~/stores/projectStore'
import { useTraceStore } from '~/stores/traceStore'
import { PROTOCOL_VERSION_TEXT } from '~/utils/protocol'
import { mergeTask } from '~/utils/tasks'

let initialized: Promise<void> | null = null
let unsubscribe: (() => void) | null = null
const streamBuffers = new Map<
  string,
  { sessionId: string; pending: string; timer?: ReturnType<typeof setTimeout> }
>()
type JsonRecord = Record<string, unknown>
const taskEventStatus: Record<string, TaskNode['status']> = {
  'task.started': 'running',
  'task.waiting': 'waiting',
  'task.completed': 'completed',
  'task.failed': 'failed',
  'task.blocked': 'blocked',
  'task.cancelled': 'cancelled',
  'task.interrupted': 'interrupted',
}
interface RuntimeUpdate {
  sessionId: string
  runId: string
  status: RunStatus
  retry_of_run_id?: string | null
  retry_task_id?: string | null
  state?: JsonRecord & {
    goal?: unknown
    report?: unknown
    tasks?: JsonRecord[]
    results?: JsonRecord[]
  }
  interruptions?: JsonRecord[]
}
interface RuntimeInitializeResult {
  databasePath?: string
  protocolVersion?: string | number
  capabilities?: string[]
}
interface McpCatalogResult {
  packages?: McpPackage[]
  pluginErrors?: Record<string, string>
}
interface McpConnectionsResult {
  packages?: McpConnection[]
}
interface McpServersResult {
  servers?: McpConnection[]
}

function isRecord(value: unknown): value is JsonRecord {
  return !!value && typeof value === 'object'
}

function records(value: unknown): JsonRecord[] {
  return Array.isArray(value) ? value.filter(isRecord) : []
}

function flushStreamBuffer(messageId: string) {
  const buffer = streamBuffers.get(messageId)
  if (!buffer) return
  const message = useSessionStore()
    .getSession(buffer.sessionId)
    ?.messages.find((item) => item.id === messageId)
  if (!message) {
    streamBuffers.delete(messageId)
    return
  }
  message.content += buffer.pending
  streamBuffers.delete(messageId)
}

function enqueueStreamDelta(sessionId: string, messageId: string, delta: string) {
  const buffer = streamBuffers.get(messageId) ?? { sessionId, pending: '' }
  buffer.pending += delta
  streamBuffers.set(messageId, buffer)
  if (!buffer.timer) buffer.timer = setTimeout(() => flushStreamBuffer(messageId), 16)
}

function completeStreamMessage(sessionId: string, messageId: string, content: string) {
  const buffer = streamBuffers.get(messageId)
  if (buffer?.timer) clearTimeout(buffer.timer)
  streamBuffers.delete(messageId)
  const message = useSessionStore()
    .getSession(sessionId)
    ?.messages.find((item) => item.id === messageId)
  if (message) {
    message.content = content
    message.status = 'completed'
  }
}

export const useRuntimeStore = defineStore('runtime', {
  state: () => ({
    connectionStatus: 'disconnected' as RuntimeInfo['status'],
    info: {
      status: 'disconnected',
      mode: 'stdio/websocket',
      version: PROTOCOL_VERSION_TEXT,
      databasePath: '',
      activeRuns: 0,
      capabilities: [],
    } as RuntimeInfo,
    approvals: [] as ApprovalRequest[],
    lastError: '',
    pendingInputs: {} as Record<string, { accepted: boolean }>,
    pendingCancellations: {} as Record<string, boolean>,
    pendingRetries: {} as Record<string, string>,
    runRequests: {} as Record<string, { requestKey: string; runId: string }>,
    packages: [] as McpPackage[],
    connections: [] as McpConnection[],
    serverConnections: [] as McpConnection[],
    pluginErrors: {} as Record<string, string>,
  }),
  getters: {
    approvalsFor: (state) => (sessionId: string) =>
      state.approvals.filter((item) => item.sessionId === sessionId && item.status === 'pending'),
    requestsFor: (state) => (sessionId: string) =>
      state.approvals.filter(
        (item) =>
          item.sessionId === sessionId && (item.status === 'pending' || item.kind === 'approval'),
      ),
    activeRunId: (state) => (sessionId: string) => {
      const session = useSessionStore().getSession(sessionId)
      const requested = session?.runs.find((run) => run.id === state.runRequests[sessionId]?.runId)
      return (
        (requested && !isTerminalStatus(requested.status) ? requested.id : '') ||
        [...(session?.runs ?? [])].reverse().find((run) => !isTerminalStatus(run.status))?.id ||
        ''
      )
    },
    retryPendingFor: (state) => (sessionId: string) =>
      Object.values(state.pendingRetries).includes(sessionId),
  },
  actions: {
    async initialize() {
      if (initialized) return initialized
      initialized = (async () => {
        this.connectionStatus = 'connecting'
        try {
          await connectRuntime()
          if (!unsubscribe) unsubscribe = onRuntimeEvent((event) => this.handleEvent(event))
          const result = await runtimeRequest<RuntimeInitializeResult>('runtime.initialize')
          this.info = {
            status: 'connected',
            mode: 'stdio/websocket',
            version: String(result.protocolVersion ?? PROTOCOL_VERSION_TEXT),
            databasePath: result.databasePath ?? '',
            activeRuns: 0,
            capabilities: result.capabilities ?? [],
          }
          await useProjectStore().loadAll()
          await useSessionStore().loadAll()
          const sessions = useSessionStore()
          this.approvals = this.approvals.filter((item) => sessions.getSession(item.sessionId))
          for (const session of sessions.sessions.filter(
            (item) => item.status === 'waiting' || item.id === sessions.activeSessionId,
          )) {
            const loaded = await useSessionStore().load(session.id)
            this.syncApprovals(loaded)
          }
          this.connectionStatus = 'connected'
        } catch (error) {
          this.connectionStatus = 'disconnected'
          this.lastError = error instanceof Error ? error.message : String(error)
          throw error
        }
      })().finally(() => {
        initialized = null
      })
      return initialized
    },
    async refreshStatus() {
      await this.initialize()
    },
    async restart() {
      this.connectionStatus = 'restarting'
      await restartRuntimeBroker()
      useSessionStore().sessions = []
      this.approvals = []
      this.pendingCancellations = {}
      this.pendingRetries = {}
      this.runRequests = {}
      useProjectStore().loaded = false
      await this.initialize()
    },
    async startRun(sessionId: string, goal: string, attachments: AttachmentRef[] = []) {
      const sessions = useSessionStore()
      const session = sessions.getSession(sessionId)
      if (!session) throw new Error('会话不存在')
      const now = new Date().toISOString()
      session.messages.push({
        id: `message_${crypto.randomUUID()}`,
        sessionId,
        role: 'user',
        content: goal,
        createdAt: now,
        status: 'completed',
        kind: 'message',
        attachments,
      })
      session.status = 'running'
      session.updatedAt = now
      const requestKey = crypto.randomUUID()
      this.runRequests[sessionId] = { requestKey, runId: '' }
      const control = this.runRequests[sessionId]!
      this.info.activeRuns += 1
      try {
        const update = await runtimeRequest<RuntimeUpdate>('run.start', {
          sessionId,
          goal,
          requestKey,
        })
        this.applyUpdate(update)
        this.syncApprovals(await sessions.load(sessionId))
        return update
      } catch (error) {
        const current = sessions.getSession(sessionId) ?? session
        const controlledRun = current.runs.find((item) => item.id === control.runId)
        if (controlledRun && isTerminalStatus(controlledRun.status))
          return { sessionId, runId: controlledRun.id, status: controlledRun.status }
        if (this.runRequests[sessionId]?.requestKey !== requestKey) throw error
        current.status = 'failed'
        const failedRun = [...current.runs]
          .reverse()
          .find((item) => ['queued', 'running', 'waiting'].includes(item.status))
        if (failedRun) {
          failedRun.status = 'failed'
          failedRun.error = error instanceof Error ? error.message : String(error)
          failedRun.updatedAt = new Date().toISOString()
        }
        current.messages.push({
          id: `message_${crypto.randomUUID()}`,
          sessionId,
          role: 'assistant',
          content: error instanceof Error ? error.message : String(error),
          createdAt: new Date().toISOString(),
          status: 'failed',
          kind: 'error',
          attachments: [],
        })
        throw error
      } finally {
        if (this.runRequests[sessionId]?.requestKey === requestKey)
          Reflect.deleteProperty(this.runRequests, sessionId)
        this.info.activeRuns = Math.max(0, this.info.activeRuns - 1)
      }
    },
    async retryRun(sessionId: string, sourceRunId: string, taskId?: string) {
      if (this.connectionStatus !== 'connected') throw new Error('运行时未连接')
      const sessions = useSessionStore()
      const session = sessions.getSession(sessionId)
      if (!session?.runs.some((run) => run.id === sourceRunId)) throw new Error('来源运行不存在')
      if (sessions.isRunning(sessionId)) throw new Error('请先结束当前运行')
      if (this.retryPendingFor(sessionId)) return
      const key = `${sourceRunId}:${taskId ?? ''}`
      this.pendingRetries[key] = sessionId
      let created = false
      try {
        const result = await runtimeRequest<{ run: JsonRecord }>('run.retry', {
          session_id: sessionId,
          run_id: sourceRunId,
          ...(taskId ? { task_id: taskId } : {}),
        })
        const run = normalizeRun(result.run, sessionId)
        created = true
        this.applyUpdate({
          sessionId,
          runId: run.id,
          status: run.status,
          state: { goal: run.objective },
          retry_of_run_id: run.retryOfRunId,
          retry_task_id: run.retryTaskId,
        })
        if (
          !isTerminalStatus(
            sessions.getSession(sessionId)?.runs.find((item) => item.id === run.id)?.status ?? '',
          )
        )
          this.runRequests[sessionId] = { requestKey: key, runId: run.id }
        this.syncApprovals(await sessions.load(sessionId))
        return run
      } catch (error) {
        if (!created) {
          try {
            this.syncApprovals(await sessions.load(sessionId))
          } catch {
            this.lastError = error instanceof Error ? error.message : String(error)
          }
        }
        throw error
      } finally {
        Reflect.deleteProperty(this.pendingRetries, key)
      }
    },
    async cancelRun(sessionId: string) {
      if (this.connectionStatus !== 'connected') throw new Error('运行时未连接')
      const runId = this.activeRunId(sessionId)
      if (!runId || this.pendingCancellations[runId]) return
      this.pendingCancellations[runId] = true
      try {
        const result = await runtimeRequest<{ accepted: boolean }>('run.cancel', {
          session_id: sessionId,
          run_id: runId,
        })
        if (!result.accepted) Reflect.deleteProperty(this.pendingCancellations, runId)
        const current = await useSessionStore().load(sessionId)
        this.syncApprovals(current)
        const run = current.runs.find((item) => item.id === runId)
        if (run && isTerminalStatus(run.status))
          this.applyUpdate({ sessionId, runId, status: run.status })
      } catch (error) {
        Reflect.deleteProperty(this.pendingCancellations, runId)
        throw error
      }
    },
    async resumeInput(id: string, response: unknown) {
      const request = this.approvals.find((item) => item.id === id)
      if (!request) throw new Error('待处理请求不存在')
      if (request.status !== 'pending' || this.pendingInputs[id]) return
      const session = useSessionStore().getSession(request.sessionId)
      const run = session?.runs.find((item) => item.id === request.runId)
      this.pendingInputs[id] = { accepted: false }
      const approval = request.kind === 'approval' || !request.kind
      if (!approval) {
        request.status = 'approved'
        if (session) session.status = 'running'
        if (run) run.status = 'running'
      }
      try {
        const update = await runtimeRequest<RuntimeUpdate>('run.resume', {
          sessionId: request.sessionId,
          runId: request.runId,
          interruptId: request.interruptId || request.id,
          response,
        })
        if (approval && request.status === 'pending')
          request.status =
            response === false || (isRecord(response) && response.approved === false)
              ? 'rejected'
              : 'approved'
        this.applyUpdate(update)
      } catch (error) {
        const currentRun = useSessionStore()
          .getSession(request.sessionId)
          ?.runs.find((item) => item.id === request.runId)
        if (currentRun && isTerminalStatus(currentRun.status)) return
        if (request.resolvedAt) throw error
        if (!this.pendingInputs[id]?.accepted) {
          request.status = 'pending'
          if (session) session.status = 'waiting'
          if (run) run.status = 'waiting'
        } else {
          if (session?.status === 'running') session.status = 'failed'
          if (run?.status === 'running') {
            run.status = 'failed'
            run.error = error instanceof Error ? error.message : String(error)
          }
        }
        throw error
      } finally {
        Reflect.deleteProperty(this.pendingInputs, id)
      }
      this.syncApprovals(await useSessionStore().load(request.sessionId))
    },
    async resolveApproval(id: string, approved: boolean) {
      const request = this.approvals.find((item) => item.id === id)
      if (!request) throw new Error('待处理请求不存在')
      if (request.status !== 'pending' || this.pendingInputs[id]) return
      if (!request.approvalId) return this.resumeInput(id, { approved })
      this.pendingInputs[id] = { accepted: false }
      const submission = this.pendingInputs[id]!
      try {
        const result = await runtimeRequest<{ accepted: boolean }>('approval.resolve', {
          session_id: request.sessionId,
          run_id: request.runId,
          approval_id: request.approvalId,
          approved,
        })
        if (!result.accepted) throw new Error('运行时未接受审批')
        if (this.pendingInputs[id]) this.pendingInputs[id]!.accepted = true
        try {
          this.syncApprovals(await useSessionStore().load(request.sessionId))
        } catch (error) {
          this.lastError = error instanceof Error ? error.message : String(error)
        }
      } catch (error) {
        if (this.pendingInputs[id] === submission) Reflect.deleteProperty(this.pendingInputs, id)
        try {
          const loaded = await useSessionStore().load(request.sessionId)
          this.syncApprovals(loaded)
          const run = loaded.runs.find((item) => item.id === request.runId)
          if (run && isTerminalStatus(run.status))
            this.applyUpdate({ sessionId: request.sessionId, runId: run.id, status: run.status })
        } catch {
          this.lastError = error instanceof Error ? error.message : String(error)
        }
        throw error
      }
    },
    syncApprovals(session: SessionRecord) {
      for (const incoming of session.approvals) {
        const existing = this.approvals.find(
          (item) =>
            item.sessionId === session.id &&
            (item.id === incoming.id ||
              (item.approvalId && item.approvalId === incoming.approvalId)),
        )
        if (!existing) this.approvals.push({ ...incoming })
        else if (
          !existing.resolvedAt &&
          (existing.status === 'pending' || incoming.status !== 'pending')
        )
          Object.assign(existing, incoming)
        if (incoming.status !== 'pending') Reflect.deleteProperty(this.pendingInputs, incoming.id)
      }
    },
    applyApprovalResolution(sessionId: string, runId: string, payload: JsonRecord) {
      const approvalId = String(payload.approval_id ?? payload.approvalId ?? '')
      const interruptId = String(payload.interrupt_id ?? payload.interruptId ?? '')
      let request = this.approvals.find(
        (item) =>
          item.sessionId === sessionId &&
          item.runId === runId &&
          ((approvalId && item.approvalId === approvalId) ||
            (interruptId && item.id === interruptId)),
      )
      if (!request) {
        request = {
          id: interruptId || approvalId,
          approvalId,
          sessionId,
          runId,
          kind: 'approval',
          action: '操作审批',
          risk: '',
          status: 'pending',
        }
        this.approvals.push(request)
      }
      if (request.resolvedAt) return
      request.resolution = String(
        payload.resolution ?? (payload.approved === true ? 'approved' : 'rejected'),
      )
      request.status =
        request.resolution === 'approved'
          ? 'approved'
          : request.resolution === 'rejected'
            ? 'rejected'
            : 'expired'
      request.resolvedAt = String(
        payload.resolved_at ?? payload.resolvedAt ?? new Date().toISOString(),
      )
      Reflect.deleteProperty(this.pendingInputs, request.id)
    },
    applyUpdate(update: RuntimeUpdate) {
      const sessions = useSessionStore()
      const session = sessions.getSession(String(update.sessionId ?? ''))
      if (!session) return
      const now = new Date().toISOString()
      const runId = String(update.runId ?? '')
      const userMessage = [...session.messages]
        .reverse()
        .find((item) => item.role === 'user' && !item.runId)
      if (userMessage) userMessage.runId = runId
      const run = session.runs.find((item) => item.id === runId)
      if (run && isTerminalStatus(run.status) && run.status !== update.status) return
      const goal = String(update.state?.goal ?? run?.objective ?? '')
      if (run) {
        run.status = update.status
        run.updatedAt = now
        run.retryOfRunId = update.retry_of_run_id ?? run.retryOfRunId
        run.retryTaskId = update.retry_task_id ?? run.retryTaskId
      } else
        session.runs.push({
          id: runId,
          sessionId: session.id,
          objective: goal,
          status: update.status,
          error: '',
          createdAt: now,
          updatedAt: now,
          retryOfRunId: update.retry_of_run_id,
          retryTaskId: update.retry_task_id,
        })
      const results = new Map(
        (update.state?.results ?? []).map((item) => [String(item.task_id ?? item.taskId), item]),
      )
      const waitingTasks = new Set(
        (update.interruptions ?? []).map((item) => item.taskId ?? item.task_id),
      )
      for (const raw of update.state?.tasks ?? []) {
        const result = results.get(String(raw.id))
        const task = session.tasks.find((item) => item.id === String(raw.id))
        if (!raw.id || (task && task.runId !== runId)) continue
        const patch = { ...raw }
        if (result) {
          const blockedBy = result.blockedBy ?? result.blocked_by ?? raw.blockedBy ?? raw.blocked_by
          patch.status = result.ok
            ? 'completed'
            : raw.status === 'blocked' || (Array.isArray(blockedBy) && blockedBy.length > 0)
              ? 'blocked'
              : 'failed'
          if (Array.isArray(blockedBy)) patch.blockedBy = blockedBy
          if (result.output !== undefined) {
            patch.output = patch.status === 'blocked' ? '' : result.output
            patch.error = result.ok ? '' : result.output
          }
        } else if (
          update.status === 'waiting' &&
          waitingTasks.has(raw.id) &&
          !isTerminalTaskStatus(String(raw.status))
        ) {
          patch.status = 'waiting'
        }
        const next = mergeTask(task, patch, session.id, runId)
        if (task) Object.assign(task, next)
        else session.tasks.push(next)
      }
      if (session.runs.at(-1)?.id === runId) session.status = update.status
      session.updatedAt = now
      if (isTerminalStatus(update.status)) {
        if (this.runRequests[session.id]?.runId === runId)
          Reflect.deleteProperty(this.runRequests, session.id)
        Reflect.deleteProperty(this.pendingCancellations, runId)
        for (const request of this.approvals.filter((item) => item.runId === runId)) {
          if (request.status === 'pending') request.status = 'expired'
          Reflect.deleteProperty(this.pendingInputs, request.id)
        }
        for (const task of session.tasks.filter((item) => item.runId === runId))
          if (!isTerminalTaskStatus(task.status)) {
            task.status = update.status
            task.statusInferred = true
          }
        for (const message of session.messages.filter(
          (item) => item.runId === runId && item.status === 'streaming',
        )) {
          const buffer = streamBuffers.get(message.id)
          if (buffer?.timer) clearTimeout(buffer.timer)
          streamBuffers.delete(message.id)
          message.status = update.status === 'cancelled' ? 'cancelled' : 'completed'
        }
      }
      if (
        update.status === 'completed' &&
        update.state?.report &&
        !session.messages.some((item) => item.runId === runId && item.role === 'assistant')
      ) {
        session.messages.push({
          id: `message_${crypto.randomUUID()}`,
          sessionId: session.id,
          runId,
          role: 'assistant',
          content: String(update.state.report),
          createdAt: now,
          status: 'completed',
          kind: 'report',
          attachments: [],
        })
      }
      if (!isTerminalStatus(update.status))
        for (const pending of update.interruptions ?? [])
          this.addInterruption(session.id, runId, pending)
    },
    addInterruption(sessionId: string, runId: string, pending: JsonRecord) {
      const approvalId = String(pending.approval_id ?? pending.approvalId ?? '')
      const id = String(pending.interruptId ?? pending.interrupt_id ?? approvalId)
      const existing = this.approvals.find(
        (item) =>
          item.sessionId === sessionId &&
          (item.id === id || (approvalId && item.approvalId === approvalId)),
      )
      if (existing) {
        if (approvalId) existing.approvalId = approvalId
        if (pending.expires_at ?? pending.expiresAt)
          existing.expiresAt = String(pending.expires_at ?? pending.expiresAt)
        return
      }
      const run = useSessionStore()
        .getSession(sessionId)
        ?.runs.find((item) => item.id === runId)
      if (run && isTerminalStatus(run.status)) return
      if (!id) return
      const kind =
        pending.kind === 'clarification' ||
        pending.kind === 'evaluation' ||
        pending.kind === 'decision'
          ? pending.kind
          : 'approval'
      this.approvals.push({
        id,
        approvalId,
        expiresAt: String(pending.expires_at ?? pending.expiresAt ?? '') || null,
        interruptId: id,
        sessionId,
        runId,
        kind,
        taskId: String(pending.task_id ?? pending.taskId ?? '') || null,
        previewRequired: Boolean(pending.previewRequired),
        actions: Array.isArray(pending.actions) ? pending.actions.map(String) : undefined,
        action: String(
          pending.action ??
            pending.tool ??
            pending.question ??
            (kind === 'approval' ? '执行受保护操作' : '需要你的输入'),
        ),
        question: String(pending.question ?? ''),
        risk: String(pending.risk ?? kind),
        details: String(pending.details ?? (pending.args ? JSON.stringify(pending.args) : '')),
        status: 'pending',
      })
    },
    async handleEvent(event: RuntimeEvent) {
      useTraceStore().handleEvent(event)
      if (event.type === 'runtime.disconnected') {
        this.connectionStatus = 'disconnected'
        this.lastError = '运行时已断开'
        return
      }
      const payload = event.payload ?? {}
      const sessionId = String(event.session_id ?? payload.sessionId ?? payload.session_id ?? '')
      const runId = String(event.run_id ?? payload.runId ?? payload.run_id ?? '')
      if (event.type === 'approval.resolved') {
        this.applyApprovalResolution(sessionId, runId, payload)
        return
      }
      const session = useSessionStore().getSession(sessionId)
      const knownRun = session?.runs.find((item) => item.id === runId)
      if (session && event.type.startsWith('task.')) {
        const taskId = String(
          event.task_id ?? payload.taskId ?? payload.task_id ?? payload.id ?? '',
        )
        const raw = isRecord(payload.task) ? { ...payload.task } : {}
        const id = taskId || String(raw.id ?? '')
        const existing = session.tasks.find((task) => task.id === id)
        if (!id || (existing && existing.runId !== runId)) return
        const status = taskEventStatus[event.type]
        if (
          knownRun &&
          isTerminalStatus(knownRun.status) &&
          !existing &&
          !isTerminalTaskStatus(status ?? String(raw.status))
        )
          return
        const task = mergeTask(
          existing,
          {
            ...raw,
            id,
            ...(payload.output !== undefined ? { output: payload.output } : {}),
            ...(payload.error !== undefined ? { error: payload.error } : {}),
          },
          sessionId,
          runId,
          status,
          event.occurred_at,
        )
        if (existing) Object.assign(existing, task)
        else session.tasks.push(task)
        return
      }
      if (knownRun && isTerminalStatus(knownRun.status)) return
      if (
        event.type === 'run.started' &&
        this.runRequests[sessionId] &&
        !this.runRequests[sessionId]!.runId
      )
        this.runRequests[sessionId]!.runId = runId
      if (event.type === 'run.resumed') {
        for (const request of this.approvals) {
          const pending = this.pendingInputs[request.id]
          if (pending && request.sessionId === sessionId && request.runId === runId)
            pending.accepted = true
        }
        if (session) {
          session.status = 'running'
          const run = session.runs.find((item) => item.id === runId)
          if (run) run.status = 'running'
        }
      }
      if (session && event.type === 'run.started') {
        const now = event.occurred_at || new Date().toISOString()
        const userMessage = [...session.messages]
          .reverse()
          .find((item) => item.role === 'user' && !item.runId)
        if (userMessage) userMessage.runId = runId
        if (!session.runs.some((item) => item.id === runId)) {
          session.runs.push({
            id: runId,
            sessionId,
            objective: String(payload.goal ?? ''),
            status: 'running',
            error: '',
            createdAt: now,
            updatedAt: now,
            retryOfRunId: String(payload.retry_of_run_id ?? '') || null,
            retryTaskId: String(payload.retry_task_id ?? '') || null,
          })
        }
        session.status = 'running'
      }
      if (
        ['run.completed', 'run.failed', 'run.cancelled', 'run.interrupted'].includes(event.type)
      ) {
        this.applyUpdate({ ...payload, sessionId, runId } as unknown as RuntimeUpdate)
        if (
          event.type === 'run.cancelled' ||
          payload.retry_of_run_id ||
          session?.runs.find((run) => run.id === runId)?.retryOfRunId
        ) {
          try {
            this.syncApprovals(await useSessionStore().load(sessionId))
          } catch (error) {
            this.lastError = error instanceof Error ? error.message : String(error)
          }
        }
      }
      if (
        [
          'approval.required',
          'approval.requested',
          'clarification.required',
          'evaluation.required',
          'run.input_required',
        ].includes(event.type)
      ) {
        this.addInterruption(sessionId, runId, payload)
      }
      if (session && event.type === 'plan.created') {
        for (const raw of records(payload.tasks)) {
          const existing = session.tasks.find((task) => task.id === String(raw.id))
          if (existing && existing.runId !== runId) continue
          const task = mergeTask(existing, raw, sessionId, runId)
          if (existing) Object.assign(existing, task)
          else session.tasks.push(task)
        }
      }
      if (session && event.type.startsWith('message.')) {
        const messageId = String(payload.messageId ?? payload.message_id ?? '')
        let message = session.messages.find((item) => item.id === messageId)
        if (!message) {
          message = {
            id: messageId || `message_${crypto.randomUUID()}`,
            sessionId,
            runId,
            role: 'assistant',
            content: '',
            createdAt: event.occurred_at || new Date().toISOString(),
            status: 'streaming',
            kind: 'report',
            attachments: [],
          }
          session.messages.push(message)
        }
        if (event.type === 'message.delta')
          enqueueStreamDelta(sessionId, message.id, String(payload.delta ?? ''))
        if (event.type === 'message.completed')
          completeStreamMessage(
            sessionId,
            message.id,
            String(
              payload.content ?? message.content + (streamBuffers.get(message.id)?.pending ?? ''),
            ),
          )
      }
    },
    async loadMcp() {
      const [catalog, connected, servers] = await Promise.all([
        runtimeRequest<McpCatalogResult>('mcp.package.catalog'),
        runtimeRequest<McpConnectionsResult>('mcp.package.list'),
        runtimeRequest<McpServersResult>('mcp.server.list'),
      ])
      this.packages = catalog.packages ?? []
      this.pluginErrors = catalog.pluginErrors ?? {}
      this.connections = connected.packages ?? []
      this.serverConnections = servers.servers ?? []
    },
    async connectPackage(packageId: string, instanceName: string, config: Record<string, unknown>) {
      await runtimeRequest('mcp.package.connect', {
        packageId,
        instanceName: instanceName || packageId,
        config,
      })
      await this.loadMcp()
    },
    async disconnectPackage(instanceName: string) {
      await runtimeRequest('mcp.package.disconnect', { instanceName })
      await this.loadMcp()
    },
    async connectServer(config: Record<string, unknown>) {
      await runtimeRequest('mcp.server.connect', config)
      await this.loadMcp()
    },
    async disconnectServer(name: string) {
      await runtimeRequest('mcp.server.disconnect', { name })
      await this.loadMcp()
    },
  },
})
