import type { PROTOCOL_VERSION } from '~/utils/protocol'

export type ConnectionStatus = 'disconnected' | 'connecting' | 'connected' | 'restarting'
export type ThemePreference = 'system' | 'light' | 'dark'
export type TaskEffort = 'low' | 'medium' | 'high'
export type TaskStatus = 'queued' | 'running' | 'waiting' | 'completed' | 'failed' | 'cancelled' | 'interrupted'
export type RunStatus = 'queued' | 'running' | 'waiting' | 'completed' | 'failed' | 'cancelled' | 'interrupted'
export type SessionStatus = 'idle' | RunStatus

export interface AttachmentRef {
  id: string
  name: string
  path: string
  size: number
  mediaType?: string | null
}

export interface ConversationMessage {
  id: string
  sessionId: string
  runId?: string | null
  role: 'user' | 'assistant' | 'system' | 'tool'
  content: string
  createdAt: string
  status: 'streaming' | 'completed' | 'failed'
  kind: 'message' | 'report' | 'error' | 'tool'
  agentRunId?: string | null
  visibility?: 'public' | 'internal'
  seq?: number
  attachments: AttachmentRef[]
}

export interface TaskNode {
  id: string
  sessionId: string
  runId: string
  parentId: string | null
  description: string
  tool: string
  effort: TaskEffort
  status: TaskStatus
  output: string
  error: string
  createdAt?: string
  updatedAt?: string
}

export interface RunRecord {
  id: string
  sessionId: string
  objective: string
  status: RunStatus
  error: string
  createdAt: string
  updatedAt: string
  retryOfRunId?: string | null
  retryTaskId?: string | null
}

export interface ProjectRecord {
  id: string
  name: string
  path: string
  isGitRepository: boolean
  writable: boolean
  createdAt: string
  updatedAt: string
}

export interface SessionRecord {
  id: string
  title: string
  projectId: string | null
  workflowId?: string | null
  nextBeforeSeq?: number | null
  createdAt: string
  updatedAt: string
  status: SessionStatus
  messages: ConversationMessage[]
  runs: RunRecord[]
  tasks: TaskNode[]
  approvals: ApprovalRequest[]
}

export interface ApprovalRequest {
  id: string
  sessionId: string
  runId: string
  taskId?: string | null
  action: string
  risk: string
  details?: string
  status: 'pending' | 'approved' | 'rejected' | 'expired'
  kind?: 'approval' | 'clarification' | 'evaluation' | 'decision'
  question?: string
  previewRequired?: boolean
  actions?: string[]
  interruptId?: string
}

export interface RuntimeInfo {
  status: ConnectionStatus
  mode: string
  version: string
  databasePath: string
  activeRuns: number
  capabilities?: string[]
}

export type GitView = 'run' | 'workspace'

export interface GitChange {
  path: string
  oldPath?: string | null
  status: 'A' | 'M' | 'D' | 'R' | 'C' | 'T' | 'U'
  staged: boolean
  unstaged: boolean
  untracked: boolean
  binary: boolean
  additions: number
  deletions: number
}

export interface GitStatus {
  view: GitView
  runId?: string | null
  branch: string
  head?: string | null
  unborn: boolean
  files: GitChange[]
  canRollback: boolean
  rollbackReason: string
}

export interface GitDiff {
  path: string
  content: string
  binary: boolean
  truncated: boolean
}

export interface McpPackage {
  id: string
  name: string
  version: string
  description: string
  configSchema: Record<string, unknown>
}

export interface McpConnection {
  name: string
  packageId?: string | null
  tools: Array<{ name: string; description: string; risk: string }>
}

export interface ModelSettings {
  provider: string
  baseUrl: string
  model: string
  hasApiKey: boolean
  mockMode: boolean
}

export interface WireRequest {
  protocol_version: typeof PROTOCOL_VERSION
  request_id: string
  method: string
  params: Record<string, unknown>
}

export interface WireError { code: string; message: string; details?: unknown }
export interface WireResponse<T = unknown> {
  protocol_version: typeof PROTOCOL_VERSION
  request_id: string
  ok: boolean
  result?: T
  error?: WireError
}

export interface RuntimeEvent {
  protocol_version: typeof PROTOCOL_VERSION
  event_id: string
  type: string
  occurred_at: string
  session_id?: string | null
  run_id?: string | null
  task_id?: string | null
  payload: Record<string, unknown>
}

export interface LegacyPersistedState {
  version: 1
  activeSessionId: string | null
  sessions: unknown[]
}
