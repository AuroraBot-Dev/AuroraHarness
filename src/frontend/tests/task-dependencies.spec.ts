import { beforeEach, expect, it, vi } from 'vitest'
import { createPinia, setActivePinia } from 'pinia'
import { useRuntimeStore } from '~/stores/runtimeStore'
import { useSessionStore } from '~/stores/sessionStore'
import type { RuntimeEvent } from '~/types/agent'

vi.mock('~/utils/runtimeClient', () => ({ runtimeRequest: vi.fn() }))

function taskEvent(type: string, task: Record<string, unknown>): RuntimeEvent {
  return {
    protocol_version: 1,
    event_id: crypto.randomUUID(),
    type,
    session_id: 'session',
    run_id: 'run',
    task_id: String(task.id),
    occurred_at: new Date().toISOString(),
    payload: { task },
  }
}

beforeEach(() => setActivePinia(createPinia()))

it('marks only the task with a pending interaction waiting', () => {
  const sessions = useSessionStore()
  const session = sessions.upsert({ id: 'session', runs: [{ id: 'run', status: 'running' }] })
  useRuntimeStore().applyUpdate({
    runId: 'run',
    sessionId: 'session',
    status: 'waiting',
    state: {
      tasks: [
        { id: 'prepare' },
        { id: 'publish', dependsOn: ['prepare'] },
        { id: 'inspect', status: 'running' },
      ],
    },
    interruptions: [{ interruptId: 'input', taskId: 'prepare' }],
  })
  expect(session.tasks.map((t) => t.status)).toEqual(['waiting', 'queued', 'running'])
  expect(session.tasks[1]?.dependsOn).toEqual(['prepare'])
})

it('keeps blocked task facts and predecessor IDs after stale events and a stale snapshot', async () => {
  const sessions = useSessionStore()
  const runtime = useRuntimeStore()
  const session = sessions.upsert({ id: 'session', runs: [{ id: 'run', status: 'running' }] })
  await runtime.handleEvent(
    taskEvent('task.created', { id: 'publish', depends_on: ['prepare', 'inspect'] }),
  )
  runtime.applyUpdate({ runId: 'run', sessionId: 'session', status: 'failed' })
  expect(session.tasks[0]?.statusInferred).toBe(true)
  await runtime.handleEvent(
    taskEvent('task.blocked', { id: 'publish', blockedBy: ['prepare'], error: '依赖失败' }),
  )
  await runtime.handleEvent(taskEvent('task.failed', { id: 'publish', error: '过时的错误' }))
  await runtime.handleEvent(
    taskEvent('task.created', { id: 'publish', dependsOn: [], blockedBy: [], status: 'queued' }),
  )
  sessions.upsert({
    id: 'session',
    runs: [{ id: 'run', status: 'failed' }],
    tasks: [{ id: 'publish', runId: 'run', status: 'failed', dependsOn: [], blockedBy: [] }],
  })
  expect(sessions.getSession('session')!.tasks[0]).toMatchObject({
    status: 'blocked',
    statusInferred: false,
    dependsOn: ['prepare', 'inspect'],
    blockedBy: ['prepare'],
    error: '依赖失败',
  })
})

it('restores a blocked result from the runtime response and accepts a late terminal task', async () => {
  const sessions = useSessionStore()
  const session = sessions.upsert({ id: 'session', runs: [{ id: 'run', status: 'running' }] })
  const runtime = useRuntimeStore()
  runtime.applyUpdate({
    sessionId: 'session',
    runId: 'run',
    status: 'failed',
    state: {
      tasks: [{ id: 'publish', dependsOn: ['prepare'] }],
      results: [{ task_id: 'publish', ok: false, output: '依赖失败', blocked_by: ['prepare'] }],
    },
  })
  expect(session.tasks[0]).toMatchObject({
    status: 'blocked',
    blockedBy: ['prepare'],
    dependsOn: ['prepare'],
  })
  await runtime.handleEvent(
    taskEvent('task.blocked', { id: 'cleanup', dependsOn: ['publish'], blockedBy: ['publish'] }),
  )
  expect(session.tasks[1]).toMatchObject({ status: 'blocked', blockedBy: ['publish'] })
})
