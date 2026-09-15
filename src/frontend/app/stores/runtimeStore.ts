import { defineStore } from 'pinia'
import type { ApprovalRequest, AttachmentRef, McpConnection, McpPackage, RunStatus, RuntimeEvent, RuntimeInfo, TaskEffort } from '~/types/agent'
import { normalizeTask } from '~/utils/normalizers'
import { connectRuntime, onRuntimeEvent, restartRuntimeBroker, runtimeRequest } from '~/utils/runtimeClient'
import { useSessionStore } from '~/stores/sessionStore'
import { useProjectStore } from '~/stores/projectStore'
import { PROTOCOL_VERSION_TEXT } from '~/utils/protocol'

let initialized: Promise<void> | null = null
let unsubscribe: (() => void) | null = null
const streamBuffers = new Map<string, { sessionId: string; pending: string; finalText?: string; timer?: ReturnType<typeof setTimeout> }>()
type JsonRecord = Record<string, unknown>
interface RuntimeUpdate {
  sessionId: string
  runId: string
  status: RunStatus
  state?: JsonRecord & { goal?: unknown; report?: unknown; tasks?: JsonRecord[]; results?: JsonRecord[] }
  interruptions?: JsonRecord[]
}
interface RuntimeInitializeResult { databasePath?: string; protocolVersion?: string | number; capabilities?: string[] }
interface McpCatalogResult { packages?: McpPackage[]; pluginErrors?: Record<string, string> }
interface McpConnectionsResult { packages?: McpConnection[] }
interface McpServersResult { servers?: McpConnection[] }

function isRecord(value: unknown): value is JsonRecord {
  return !!value && typeof value === 'object'
}

function records(value: unknown): JsonRecord[] {
  return Array.isArray(value) ? value.filter(isRecord) : []
}

function flushStreamBuffer(messageId: string) {
  const buffer = streamBuffers.get(messageId)
  if (!buffer) return
  const message = useSessionStore().getSession(buffer.sessionId)?.messages.find((item) => item.id === messageId)
  if (!message) { streamBuffers.delete(messageId); return }
  const delta = buffer.pending.slice(0, 8)
  buffer.pending = buffer.pending.slice(delta.length)
  message.content += delta
  if (buffer.pending) {
    buffer.timer = setTimeout(() => flushStreamBuffer(messageId), 16)
  } else if (buffer.finalText !== undefined) {
    message.content = buffer.finalText
    message.status = 'completed'
    streamBuffers.delete(messageId)
  } else {
    buffer.timer = undefined
  }
}

function enqueueStreamDelta(sessionId: string, messageId: string, delta: string) {
  const buffer = streamBuffers.get(messageId) ?? { sessionId, pending: '' }
  buffer.pending += delta
  streamBuffers.set(messageId, buffer)
  if (!buffer.timer) flushStreamBuffer(messageId)
}

function completeStreamMessage(sessionId: string, messageId: string, content: string) {
  const buffer = streamBuffers.get(messageId) ?? { sessionId, pending: '' }
  buffer.finalText = content
  streamBuffers.set(messageId, buffer)
  if (!buffer.timer) flushStreamBuffer(messageId)
}

export const useRuntimeStore = defineStore('runtime', {
  state: () => ({
    connectionStatus: 'disconnected' as RuntimeInfo['status'],
    info: { status: 'disconnected', mode: 'stdio/websocket', version: PROTOCOL_VERSION_TEXT, databasePath: '', activeRuns: 0, capabilities: [] } as RuntimeInfo,
    approvals: [] as ApprovalRequest[], lastError: '',
    packages: [] as McpPackage[], connections: [] as McpConnection[], serverConnections: [] as McpConnection[], pluginErrors: {} as Record<string, string>,
  }),
  getters: {
    approvalsFor: (state) => (sessionId: string) => state.approvals.filter((item) => item.sessionId === sessionId && item.status === 'pending'),
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
          this.info = { status: 'connected', mode: 'stdio/websocket', version: String(result.protocolVersion ?? PROTOCOL_VERSION_TEXT), databasePath: result.databasePath ?? '', activeRuns: 0, capabilities: result.capabilities ?? [] }
          await useProjectStore().loadAll()
          await useSessionStore().loadAll()
          this.approvals = []
          for (const session of useSessionStore().sessions.filter((item) => item.status === 'waiting')) {
            const loaded = await useSessionStore().load(session.id)
            for (const pending of loaded.approvals.filter((item) => item.status === 'pending')) this.approvals.push({ ...pending, id: pending.interruptId || pending.id })
          }
          this.connectionStatus = 'connected'
        } catch (error) {
          this.connectionStatus = 'disconnected'
          this.lastError = error instanceof Error ? error.message : String(error)
          throw error
        }
      })().finally(() => { initialized = null })
      return initialized
    },
    async refreshStatus() { await this.initialize() },
    async restart() {
      this.connectionStatus = 'restarting'
      await restartRuntimeBroker()
      useSessionStore().sessions = []
      this.approvals = []
      useProjectStore().loaded = false
      await this.initialize()
    },
    async startRun(sessionId: string, goal: string, attachments: AttachmentRef[] = []) {
      const sessions = useSessionStore()
      const session = sessions.getSession(sessionId)
      if (!session) throw new Error('会话不存在')
      const now = new Date().toISOString()
      session.messages.push({
        id: `message_${crypto.randomUUID()}`, sessionId, role: 'user', content: goal,
        createdAt: now, status: 'completed', kind: 'message', attachments,
      })
      session.status = 'running'; session.updatedAt = now; this.info.activeRuns += 1
      try {
        const update = await runtimeRequest<RuntimeUpdate>('run.start', { sessionId, goal, requestKey: crypto.randomUUID() })
        this.applyUpdate(update)
        await sessions.load(sessionId)
        return update
      } catch (error) {
        session.status = 'failed'
        const failedRun = [...session.runs].reverse().find((item) => ['queued', 'running', 'waiting'].includes(item.status))
        if (failedRun) { failedRun.status = 'failed'; failedRun.error = error instanceof Error ? error.message : String(error); failedRun.updatedAt = new Date().toISOString() }
        session.messages.push({
          id: `message_${crypto.randomUUID()}`, sessionId, role: 'assistant', content: error instanceof Error ? error.message : String(error),
          createdAt: new Date().toISOString(), status: 'failed', kind: 'error', attachments: [],
        })
        throw error
      } finally { this.info.activeRuns = Math.max(0, this.info.activeRuns - 1) }
    },
    async resumeInput(id: string, response: unknown) {
      const request = this.approvals.find((item) => item.id === id)
      if (!request) throw new Error('待处理请求不存在')
      try {
        const update = await runtimeRequest<RuntimeUpdate>('run.resume', {
          sessionId: request.sessionId, runId: request.runId,
          interruptId: request.interruptId || request.id, response,
        })
        request.status = response === false || (isRecord(response) && response.approved === false) ? 'rejected' : 'approved'
        this.applyUpdate(update)
        await useSessionStore().load(request.sessionId)
      } catch (error) {
        const session = useSessionStore().getSession(request.sessionId)
        const run = session?.runs.find((item) => item.id === request.runId)
        if (session) session.status = 'failed'
        if (run) { run.status = 'failed'; run.error = error instanceof Error ? error.message : String(error); run.updatedAt = new Date().toISOString() }
        throw error
      }
    },
    async resolveApproval(id: string, approved: boolean) { await this.resumeInput(id, { approved }) },
    applyUpdate(update: RuntimeUpdate) {
      const sessions = useSessionStore()
      const session = sessions.getSession(String(update.sessionId ?? ''))
      if (!session) return
      const now = new Date().toISOString()
      const runId = String(update.runId ?? '')
      const userMessage = [...session.messages].reverse().find((item) => item.role === 'user' && !item.runId)
      if (userMessage) userMessage.runId = runId
      const run = session.runs.find((item) => item.id === runId)
      const goal = String(update.state?.goal ?? run?.objective ?? '')
      if (run) { run.status = update.status; run.updatedAt = now }
      else session.runs.push({ id: runId, sessionId: session.id, objective: goal, status: update.status, error: '', createdAt: now, updatedAt: now })
      const results = new Map((update.state?.results ?? []).map((item) => [String(item.task_id), item]))
      for (const raw of update.state?.tasks ?? []) {
        const result = results.get(String(raw.id))
        const task = session.tasks.find((item) => item.id === String(raw.id))
        const next = {
          id: String(raw.id), sessionId: session.id, runId, parentId: null,
          description: String(raw.description ?? '未命名任务'), tool: String(raw.tool ?? ''), effort: (['low', 'medium', 'high'].includes(String(raw.effort)) ? raw.effort : 'medium') as TaskEffort,
          status: result ? (result.ok ? 'completed' : 'failed') : update.status === 'waiting' ? 'waiting' : 'queued',
          output: String(result?.output ?? ''), error: result?.ok === false ? String(result.output ?? '') : '',
          createdAt: task?.createdAt ?? now, updatedAt: now,
        } as const
        if (task) Object.assign(task, next); else session.tasks.push(next)
      }
      session.status = update.status
      session.updatedAt = now
      if (update.status === 'completed' && update.state?.report && !session.messages.some((item) => item.runId === runId && item.role === 'assistant')) {
        session.messages.push({
          id: `message_${crypto.randomUUID()}`, sessionId: session.id, runId, role: 'assistant', content: String(update.state.report),
          createdAt: now, status: 'completed', kind: 'report', attachments: [],
        })
      }
      for (const pending of update.interruptions ?? []) this.addInterruption(session.id, runId, pending)
    },
    addInterruption(sessionId: string, runId: string, pending: JsonRecord) {
      const id = String(pending.interruptId ?? '')
      if (!id || this.approvals.some((item) => item.id === id)) return
      const kind = pending.kind === 'clarification' || pending.kind === 'evaluation' || pending.kind === 'decision' ? pending.kind : 'approval'
      this.approvals.push({
        id, interruptId: id, sessionId, runId, kind,
        previewRequired: Boolean(pending.previewRequired),
        actions: Array.isArray(pending.actions) ? pending.actions.map(String) : undefined,
        action: String(pending.action ?? pending.question ?? (kind === 'approval' ? '执行受保护操作' : '需要你的输入')),
        question: String(pending.question ?? ''), risk: String(pending.risk ?? kind),
        details: String(pending.details ?? (pending.args ? JSON.stringify(pending.args) : '')), status: 'pending',
      })
    },
    async handleEvent(event: RuntimeEvent) {
      if (event.type === 'runtime.disconnected') { this.connectionStatus = 'disconnected'; this.lastError = '运行时已断开'; return }
      const payload = event.payload ?? {}
      const sessionId = String(event.session_id ?? payload.sessionId ?? payload.session_id ?? '')
      const runId = String(event.run_id ?? payload.runId ?? payload.run_id ?? '')
      const session = useSessionStore().getSession(sessionId)
      if (session && event.type === 'run.started') {
        const now = event.occurred_at || new Date().toISOString()
        const userMessage = [...session.messages].reverse().find((item) => item.role === 'user' && !item.runId)
        if (userMessage) userMessage.runId = runId
        if (!session.runs.some((item) => item.id === runId)) {
          session.runs.push({ id: runId, sessionId, objective: String(payload.goal ?? ''), status: 'running', error: '', createdAt: now, updatedAt: now })
        }
        session.status = 'running'
      }
      if (['run.completed', 'run.failed', 'run.cancelled', 'run.interrupted'].includes(event.type)) this.applyUpdate(payload as unknown as RuntimeUpdate)
      if (['approval.required', 'clarification.required', 'evaluation.required', 'run.input_required'].includes(event.type)) {
        this.addInterruption(sessionId, runId, payload)
      }
      if (session && event.type === 'plan.created') {
        for (const raw of records(payload.tasks)) {
          const task = normalizeTask(raw, sessionId, runId)
          const index = session.tasks.findIndex((item) => item.id === task.id)
          if (index >= 0) session.tasks[index] = task; else session.tasks.push(task)
        }
      }
      if (session && event.type.startsWith('task.')) {
        const taskId = String(event.task_id ?? payload.taskId ?? payload.task_id ?? payload.id ?? '')
        const task = session.tasks.find((item) => item.id === taskId)
        if (task && event.type === 'task.started') task.status = 'running'
        if (task && event.type === 'task.completed') { task.status = 'completed'; task.output = String(payload.output ?? '') }
        if (task && event.type === 'task.failed') { task.status = 'failed'; task.error = String(payload.error ?? '') }
      }
      if (session && event.type.startsWith('message.')) {
        const messageId = String(payload.messageId ?? payload.message_id ?? '')
        let message = session.messages.find((item) => item.id === messageId)
        if (!message) {
          message = {
            id: messageId || `message_${crypto.randomUUID()}`, sessionId, runId, role: 'assistant', content: '',
            createdAt: event.occurred_at || new Date().toISOString(), status: 'streaming', kind: 'report', attachments: [],
          }
          session.messages.push(message)
        }
        if (event.type === 'message.delta') enqueueStreamDelta(sessionId, message.id, String(payload.delta ?? ''))
        if (event.type === 'message.completed') completeStreamMessage(sessionId, message.id, String(payload.content ?? message.content))
      }
    },
    async loadMcp() {
      const [catalog, connected, servers] = await Promise.all([
        runtimeRequest<McpCatalogResult>('mcp.package.catalog'), runtimeRequest<McpConnectionsResult>('mcp.package.list'), runtimeRequest<McpServersResult>('mcp.server.list'),
      ])
      this.packages = catalog.packages ?? []; this.pluginErrors = catalog.pluginErrors ?? {}; this.connections = connected.packages ?? []; this.serverConnections = servers.servers ?? []
    },
    async connectPackage(packageId: string, instanceName: string, config: Record<string, unknown>) {
      await runtimeRequest('mcp.package.connect', { packageId, instanceName: instanceName || packageId, config })
      await this.loadMcp()
    },
    async disconnectPackage(instanceName: string) { await runtimeRequest('mcp.package.disconnect', { instanceName }); await this.loadMcp() },
    async connectServer(config: Record<string, unknown>) { await runtimeRequest('mcp.server.connect', config); await this.loadMcp() },
    async disconnectServer(name: string) { await runtimeRequest('mcp.server.disconnect', { name }); await this.loadMcp() },
  },
})
