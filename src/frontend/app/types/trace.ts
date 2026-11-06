import type { AgentExecution } from '~/types/workflow'

export interface ToolCallTrace {
  id: string
  runId: string
  agentRunId: string
  taskId: string | null
  taskKey: string
  toolName: string
  input: unknown
  output: string
  error: string
  status: string
  startedAt: string
  endedAt: string | null
  durationMs: number | null
}

export interface RunTrace {
  calls: ToolCallTrace[]
  stages: AgentExecution[]
  loaded: boolean
  loading: boolean
  refreshQueued: boolean
  error: string
  revision: number
}
