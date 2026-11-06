import { defineStore } from 'pinia'
import type { RuntimeEvent } from '~/types/agent'
import type { RunTrace } from '~/types/trace'
import type { AgentExecution } from '~/types/workflow'
import { isTerminalStatus } from '~/utils/normalizers'
import { normalizeToolCall } from '~/utils/trace'
import { runtimeRequest } from '~/utils/runtimeClient'

export const useTraceStore = defineStore('trace', {
  state: () => ({ runs: {} as Record<string, RunTrace> }),
  actions: {
    ensure(runId: string): RunTrace {
      if (!this.runs[runId])
        this.runs[runId] = {
          calls: [],
          stages: [],
          loaded: false,
          loading: false,
          refreshQueued: false,
          error: '',
          revision: 0,
        }
      return this.runs[runId]!
    },
    mergeCall(runId: string, raw: Record<string, unknown>) {
      const trace = this.ensure(runId)
      const incoming = normalizeToolCall({ ...raw, runId })
      if (!incoming.id) return
      const existing = trace.calls.find((call) => call.id === incoming.id)
      if (!existing) trace.calls.push(incoming)
      else if (!isTerminalStatus(existing.status) || incoming.status === existing.status)
        Object.assign(existing, incoming)
      else if (incoming.taskId) existing.taskId = incoming.taskId
      trace.calls.sort(
        (left, right) =>
          left.startedAt.localeCompare(right.startedAt) || left.id.localeCompare(right.id),
      )
    },
    handleEvent(event: RuntimeEvent) {
      const runId = String(event.run_id ?? event.payload.run_id ?? event.payload.runId ?? '')
      if (!runId) return
      if (event.type.startsWith('tool.')) {
        this.mergeCall(runId, event.payload)
        const trace = this.ensure(runId)
        const agentRunId = String(event.payload.agent_run_id ?? event.payload.agentRunId ?? '')
        if (agentRunId && !trace.stages.some((stage) => stage.id === agentRunId)) trace.revision++
      }
      if (/^(stage|run|approval)\./.test(event.type)) this.ensure(runId).revision++
    },
    async load(runId: string) {
      const trace = this.ensure(runId)
      if (trace.loading) {
        trace.refreshQueued = true
        return
      }
      trace.loading = true
      try {
        const detail = await runtimeRequest<{
          toolCalls?: Record<string, unknown>[]
          agentRuns?: AgentExecution[]
        }>('run.get', { runId })
        if (this.runs[runId] !== trace) return
        for (const call of detail.toolCalls ?? []) this.mergeCall(runId, call)
        for (const stage of detail.agentRuns ?? []) {
          const existing = trace.stages.find((item) => item.id === stage.id)
          if (!existing) trace.stages.push(stage)
          else if (!isTerminalStatus(existing.status) || isTerminalStatus(stage.status))
            Object.assign(existing, stage)
        }
        trace.loaded = true
        trace.error = ''
      } catch (error) {
        trace.error = error instanceof Error ? error.message : String(error)
      } finally {
        trace.loading = false
        if (trace.refreshQueued && this.runs[runId] === trace) {
          trace.refreshQueued = false
          void this.load(runId)
        }
      }
    },
    remove(runIds: string[]) {
      for (const id of runIds) Reflect.deleteProperty(this.runs, id)
    },
  },
})
