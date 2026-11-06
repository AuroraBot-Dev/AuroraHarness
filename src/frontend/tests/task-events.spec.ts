import { beforeEach, describe, expect, it, vi } from 'vitest'
import { createPinia, setActivePinia } from 'pinia'
import { useRuntimeStore } from '~/stores/runtimeStore'
import { useSessionStore } from '~/stores/sessionStore'
import { taskForest } from '~/utils/tasks'
import { normalizeTask } from '~/utils/normalizers'
import type { RuntimeEvent } from '~/types/agent'

vi.mock('~/utils/runtimeClient', () => ({ runtimeRequest: vi.fn() }))

function event(type: string, task: Record<string, unknown>, time = '2026-11-07T10:00:00Z'): RuntimeEvent {
  return { protocol_version: 1, event_id: crypto.randomUUID(), occurred_at: time, type, session_id: 's', run_id: 'r', task_id: String(task.id), payload: { task } }
}

function setup() {
  const sessions = useSessionStore()
  const session = sessions.upsert({ id: 's', status: 'running', runs: [{ id: 'r', status: 'running' }], tasks: [] })
  return { sessions, session, runtime: useRuntimeStore() }
}

describe('recursive task events', () => {
  beforeEach(() => setActivePinia(createPinia()))

  it('creates a placeholder for an early start and hydrates a late creation without regressing it', async () => {
    const { session, runtime } = setup()
    await runtime.handleEvent(event('task.started', { id: 'leaf' }))
    await runtime.handleEvent(event('task.created', { id: 'leaf', parent_id: 'parent', description: '深层叶子', tool: 'write_file', status: 'queued' }, '2026-11-07T09:59:59Z'))
    expect(session.tasks).toHaveLength(1)
    expect(session.tasks[0]).toMatchObject({ status: 'running', description: '深层叶子', parentId: 'parent', tool: 'write_file' })
    expect(taskForest(session.tasks)[0]?.parentId).toBeNull()
    await runtime.handleEvent(event('task.created', { id: 'parent', description: '父任务', parent_id: null }))
    expect(taskForest(session.tasks).find(task => task.id === 'leaf')?.parentId).toBe('parent')
  })

  it('keeps a terminal child across duplicate, late start, plan and snapshot updates', async () => {
    const { sessions, session, runtime } = setup()
    const completed = event('task.completed', { id: 'leaf', parent_id: 'parent', output: '真实完成', status: 'completed' })
    await runtime.handleEvent(completed)
    await runtime.handleEvent(completed)
    await runtime.handleEvent(event('task.started', { id: 'leaf' }, '2026-11-07T10:00:01Z'))
    await runtime.handleEvent({ ...event('plan.created', {}), payload: { tasks: [{ id: 'leaf', status: 'queued' }] } })
    expect(session.tasks).toHaveLength(1)
    expect(session.tasks[0]).toMatchObject({ status: 'completed', output: '真实完成', parentId: 'parent' })
    sessions.upsert({ id: 's', runs: [{ id: 'r', status: 'running' }], tasks: [{ id: 'leaf', runId: 'r', status: 'waiting', output: '' }] })
    expect(sessions.getSession('s')!.tasks[0]).toMatchObject({ status: 'completed', output: '真实完成', parentId: 'parent' })
  })

  it('hydrates late parent metadata after the run ends while keeping the terminal task result', async () => {
    const { session, runtime } = setup()
    await runtime.handleEvent(event('task.completed', { id: 'leaf', output: 'done' }))
    runtime.applyUpdate({ sessionId: 's', runId: 'r', status: 'completed' })
    await runtime.handleEvent(event('task.created', { id: 'leaf', description: '补齐描述', parent_id: 'parent', status: 'queued' }))
    expect(session.tasks[0]).toMatchObject({ status: 'completed', output: 'done', description: '补齐描述', parentId: 'parent' })
  })

  it('does not modify a task belonging to a different run', async () => {
    const { session, runtime } = setup()
    session.tasks.push(normalizeTask({ id: 'leaf', status: 'completed' }, 's', 'old'))
    await runtime.handleEvent(event('task.failed', { id: 'leaf', error: 'wrong run' }))
    expect(session.tasks).toHaveLength(1)
    expect(session.tasks[0]).toMatchObject({ runId: 'old', status: 'completed', error: '' })
  })

  it('uses a late factual completion to correct a status inferred from the run ending', async () => {
    const { sessions, session, runtime } = setup()
    await runtime.handleEvent(event('task.started', { id: 'leaf' }))
    runtime.applyUpdate({ sessionId: 's', runId: 'r', status: 'failed' })
    expect(session.tasks[0]).toMatchObject({ status: 'failed', statusInferred: true })
    await runtime.handleEvent(event('task.completed', { id: 'leaf', output: '实际已完成', status: 'completed' }))
    expect(session.tasks[0]).toMatchObject({ status: 'completed', output: '实际已完成', statusInferred: false })
    sessions.upsert({ id: 's', runs: [{ id: 'r', status: 'failed' }], tasks: [{ id: 'leaf', runId: 'r', status: 'running' }] })
    expect(sessions.getSession('s')!.tasks[0]).toMatchObject({ status: 'completed', output: '实际已完成' })
  })

  it('reparents arbitrary-depth orphans when ancestors arrive and cuts invalid cycles without mutating history', () => {
    const tasks = [
      normalizeTask({ id: 'a', parentId: 'b' }, 's', 'r'),
      normalizeTask({ id: 'b', parentId: 'c' }, 's', 'r'),
      normalizeTask({ id: 'c', parentId: 'a' }, 's', 'r'),
      normalizeTask({ id: 'other', parentId: 'a' }, 's', 'other-run'),
    ]
    const forest = taskForest(tasks)
    expect(forest.filter(task => task.parentId === null).map(task => task.id)).toEqual(['a', 'other'])
    expect(tasks[0]?.parentId).toBe('b')
    expect(forest.find(task => task.id === 'b')?.parentId).toBe('c')
  })
})
