import type { TaskNode, TaskStatus } from '~/types/agent'
import { isTerminalTaskStatus, normalizeTask } from '~/utils/normalizers'

type Json = Record<string, unknown>
const fields: Record<string, string[]> = {
  description: ['description'],
  tool: ['tool'],
  effort: ['effort'],
  parentId: ['parentId', 'parent_id'],
  retryOfTaskId: ['retryOfTaskId', 'retry_of_task_id', 'source_task_id'],
  createdAt: ['createdAt', 'created_at'],
}

export function mergeTask(
  existing: TaskNode | undefined,
  raw: Json,
  sessionId: string,
  runId: string,
  status?: TaskStatus,
  timestamp?: string,
): TaskNode {
  const incoming = normalizeTask(raw, sessionId, runId)
  incoming.runId = runId
  incoming.sessionId = sessionId
  if (status) incoming.status = status
  if (timestamp) incoming.updatedAt = timestamp
  if (!existing) return incoming
  const result = {
    ...existing,
    dependsOn: [...new Set([...existing.dependsOn, ...incoming.dependsOn])],
    blockedBy: [...new Set([...existing.blockedBy, ...incoming.blockedBy])],
  }
  for (const [field, aliases] of Object.entries(fields)) {
    if (field === 'parentId' && existing.parentId && !incoming.parentId) continue
    if (aliases.some((key) => raw[key] !== undefined))
      Object.assign(result, { [field]: incoming[field as keyof TaskNode] })
  }
  const older =
    !!incoming.updatedAt &&
    !!existing.updatedAt &&
    Date.parse(incoming.updatedAt) < Date.parse(existing.updatedAt)
  const terminal = isTerminalTaskStatus(existing.status)
  const regressesToQueue = existing.status !== 'queued' && incoming.status === 'queued'
  const correctsInference = !!existing.statusInferred && isTerminalTaskStatus(incoming.status)
  if (
    (!older || correctsInference) &&
    !regressesToQueue &&
    (!terminal || incoming.status === existing.status || correctsInference)
  ) {
    result.status = incoming.status
    if (existing.statusInferred) result.statusInferred = raw.statusInferred === true
    if (raw.output !== undefined) result.output = incoming.output
    if (raw.error !== undefined) result.error = incoming.error
    if (incoming.updatedAt) result.updatedAt = incoming.updatedAt
  }
  return result
}

export function taskForest(tasks: TaskNode[]): TaskNode[] {
  const result = tasks.map((task) => ({ ...task }))
  const byId = new Map(result.map((task) => [task.id, task]))
  for (const task of result) {
    const parent = task.parentId ? byId.get(task.parentId) : undefined
    if (!parent || parent.runId !== task.runId) task.parentId = null
  }
  for (const task of result) {
    const seen = new Set([task.id])
    let parent = task.parentId
    while (parent) {
      if (seen.has(parent)) {
        task.parentId = null
        break
      }
      seen.add(parent)
      parent = byId.get(parent)?.parentId ?? null
    }
  }
  return result
}
