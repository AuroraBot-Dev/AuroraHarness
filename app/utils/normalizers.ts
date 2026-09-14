import type {
  AttachmentRef,
  ConversationMessage,
  ModelSettings,
  ProjectRecord,
  RunRecord,
  RuntimeInfo,
  SessionRecord,
  TaskNode,
} from '~/types/agent'

type Json = Record<string, unknown>
const text = (value: unknown, fallback = '') => typeof value === 'string' ? value : fallback
const list = (value: unknown): Json[] => Array.isArray(value) ? value.filter((item): item is Json => !!item && typeof item === 'object') : []
const choice = <T extends string>(value: unknown, values: readonly T[], fallback: T): T => typeof value === 'string' && values.includes(value as T) ? value as T : fallback

export function normalizeAttachment(raw: Json): AttachmentRef {
  return {
    id: text(raw.id, crypto.randomUUID()),
    name: text(raw.name, text(raw.filename, '附件')),
    path: text(raw.path),
    size: Number(raw.size ?? 0),
    mediaType: text(raw.media_type ?? raw.mime_type ?? raw.mediaType) || null,
  }
}

export function normalizeMessage(raw: Json, sessionId: string): ConversationMessage {
  return {
    id: text(raw.id, crypto.randomUUID()),
    sessionId: text(raw.session_id ?? raw.sessionId, sessionId),
    runId: text(raw.run_id ?? raw.runId) || null,
    role: choice(raw.role, ['user', 'assistant', 'system', 'tool'], 'system'),
    content: text(raw.content),
    createdAt: text(raw.created_at ?? raw.createdAt, new Date().toISOString()),
    status: choice(raw.status, ['streaming', 'completed', 'failed'], 'completed'),
    kind: choice(raw.kind, ['message', 'report', 'error', 'tool'], 'message'),
    agentRunId: text(raw.agentRunId ?? raw.agent_run_id) || null,
    visibility: choice(raw.visibility, ['public', 'internal'] as const, 'public'),
    seq: Number(raw.seq ?? 0),
    attachments: list(raw.attachments).map(normalizeAttachment),
  }
}

export function normalizeRun(raw: Json, sessionId: string): RunRecord {
  return {
    id: text(raw.id), sessionId: text(raw.session_id ?? raw.sessionId, sessionId),
    objective: text(raw.objective), status: choice(raw.status, ['queued', 'running', 'waiting', 'completed', 'failed', 'cancelled', 'interrupted'], 'queued'), error: text(raw.error),
    createdAt: text(raw.created_at ?? raw.createdAt, new Date().toISOString()),
    updatedAt: text(raw.updated_at ?? raw.updatedAt, new Date().toISOString()),
    retryOfRunId: text(raw.retry_of_run_id ?? raw.retryOfRunId) || null,
    retryTaskId: text(raw.retry_task_id ?? raw.retryTaskId) || null,
  }
}

export function normalizeTask(raw: Json, sessionId: string, runId = ''): TaskNode {
  return {
    id: text(raw.id), sessionId: text(raw.session_id ?? raw.sessionId, sessionId),
    runId: text(raw.run_id ?? raw.runId, runId),
    parentId: text(raw.parent_id ?? raw.parentId) || null,
    description: text(raw.description, '未命名任务'),
    tool: text(raw.tool),
    effort: choice(raw.effort, ['low', 'medium', 'high'], 'medium'),
    status: choice(raw.status, ['queued', 'running', 'waiting', 'completed', 'failed', 'cancelled', 'interrupted'], 'queued'), output: text(raw.output), error: text(raw.error),
    createdAt: text(raw.created_at ?? raw.createdAt), updatedAt: text(raw.updated_at ?? raw.updatedAt),
  }
}

export function normalizeProject(raw: Json): ProjectRecord {
  return {
    id: text(raw.id),
    name: text(raw.name, '未命名项目'),
    path: text(raw.path),
    isGitRepository: Boolean(raw.isGitRepository ?? raw.is_git_repository),
    writable: Boolean(raw.writable),
    createdAt: text(raw.created_at ?? raw.createdAt, new Date().toISOString()),
    updatedAt: text(raw.updated_at ?? raw.updatedAt, new Date().toISOString()),
  }
}

export function normalizeSession(raw: Json): SessionRecord {
  const id = text(raw.id)
  const runs = list(raw.runs).map((item) => normalizeRun(item, id))
  const latestRun = runs.at(-1)?.id ?? ''
  const nestedTasks = list(raw.runs).flatMap((run) => list(run.tasks).map((item) => normalizeTask(item, id, text(run.id))))
  return {
    id,
    title: text(raw.title, '未命名会话'),
    projectId: text(raw.project_id ?? raw.projectId) || null,
    workflowId: text(raw.workflowId) || null,
    nextBeforeSeq: typeof raw.nextBeforeSeq === 'number' ? raw.nextBeforeSeq : null,
    createdAt: text(raw.created_at ?? raw.createdAt, new Date().toISOString()),
    updatedAt: text(raw.updated_at ?? raw.updatedAt, new Date().toISOString()),
    status: choice(raw.status ?? raw.run_status, ['idle', 'queued', 'running', 'waiting', 'completed', 'failed', 'cancelled', 'interrupted'], runs.at(-1)?.status ?? 'idle'),
    messages: list(raw.messages).map((item) => normalizeMessage(item, id)),
    runs,
    tasks: list(raw.tasks).map((item) => normalizeTask(item, id, latestRun)).concat(nestedTasks),
    approvals: list(raw.approvals).map((item) => ({
      id: text(item.id), sessionId: text(item.sessionId ?? item.session_id, id), runId: text(item.runId ?? item.run_id), taskId: text(item.task_id) || null,
      kind: choice(item.kind, ['approval', 'clarification', 'evaluation', 'decision'] as const, 'approval'),
      previewRequired: Boolean(item.previewRequired),
      question: text(item.question), interruptId: text(item.interruptId),
      actions: Array.isArray(item.actions) ? item.actions.map(String) : undefined,
      action: text(item.action ?? item.tool), risk: text(item.risk), status: choice(item.status, ['pending', 'approved', 'rejected', 'expired'], 'expired'),
    })),
  }
}

export function normalizeRuntime(raw: Json): RuntimeInfo {
  const database = raw.database && typeof raw.database === 'object' ? raw.database as Json : {}
  return {
    status: raw.connected === false || raw.status === 'disconnected' ? 'disconnected' : 'connected',
    mode: text(raw.mode, 'mock'), version: text(raw.version, '1'),
    databasePath: text(raw.database_path ?? raw.databasePath ?? database.path), activeRuns: Number(raw.active_runs ?? raw.activeRuns ?? 0),
    capabilities: Array.isArray(raw.capabilities) ? raw.capabilities.map(String) : [],
  }
}

export function normalizeSettings(raw: Json): ModelSettings {
  return {
    provider: text(raw.provider, 'mock'), baseUrl: text(raw.base_url ?? raw.baseUrl),
    model: text(raw.model, 'mock-agent-v1'), hasApiKey: Boolean(raw.has_api_key ?? raw.api_key_configured ?? raw.hasApiKey),
    mockMode: Boolean(raw.mock_mode ?? raw.mockMode ?? raw.provider === 'mock'),
  }
}
