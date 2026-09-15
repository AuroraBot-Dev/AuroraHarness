import { beforeEach, describe, expect, it } from 'vitest'
import { createPinia, setActivePinia } from 'pinia'
import { useRuntimeStore } from '~/stores/runtimeStore'
import { useSessionStore } from '~/stores/sessionStore'

describe('runtime event isolation', () => {
  beforeEach(() => setActivePinia(createPinia()))
  it('merges events by session and run instead of arrival order', async () => {
    const sessions = useSessionStore()
    sessions.upsert({ id: 'a', title: 'A', runs: [], messages: [], tasks: [] })
    sessions.upsert({ id: 'b', title: 'B', runs: [], messages: [], tasks: [] })
    const runtime = useRuntimeStore()
    await runtime.handleEvent({ protocol_version: 1, event_id: '1', type: 'plan.created', occurred_at: '2026-01-01', session_id: 'b', run_id: 'rb', payload: { tasks: [{ id: 'tb', session_id: 'b', run_id: 'rb', description: 'B task', effort: 'high', status: 'queued' }] } })
    await runtime.handleEvent({ protocol_version: 1, event_id: '2', type: 'task.started', occurred_at: '2026-01-01', session_id: 'b', run_id: 'rb', payload: { task_id: 'tb' } })
    expect(sessions.getSession('b')?.tasks[0]?.status).toBe('running')
    expect(sessions.getSession('a')?.tasks).toHaveLength(0)
  })

  it('appends streamed assistant deltas to one message', async () => {
    const sessions = useSessionStore()
    sessions.upsert({ id: 's1', title: '流式会话', runs: [], messages: [], tasks: [] })
    const runtime = useRuntimeStore()
    await runtime.handleEvent({ protocol_version: 1, event_id: 'start', type: 'run.started', occurred_at: '2026-01-01', session_id: 's1', run_id: 'r1', payload: { goal: '测试' } })
    await runtime.handleEvent({ protocol_version: 1, event_id: 'm1', type: 'message.started', occurred_at: '2026-01-01', session_id: 's1', run_id: 'r1', payload: { messageId: 'answer' } })
    await runtime.handleEvent({ protocol_version: 1, event_id: 'm2', type: 'message.delta', occurred_at: '2026-01-01', session_id: 's1', run_id: 'r1', payload: { messageId: 'answer', delta: '第一段' } })
    await runtime.handleEvent({ protocol_version: 1, event_id: 'm3', type: 'message.delta', occurred_at: '2026-01-01', session_id: 's1', run_id: 'r1', payload: { messageId: 'answer', delta: '第二段' } })
    await runtime.handleEvent({ protocol_version: 1, event_id: 'm4', type: 'message.completed', occurred_at: '2026-01-01', session_id: 's1', run_id: 'r1', payload: { messageId: 'answer', content: '第一段第二段' } })
    await new Promise((resolve) => setTimeout(resolve, 40))

    const message = sessions.getSession('s1')?.messages[0]
    expect(message?.content).toBe('第一段第二段')
    expect(message?.status).toBe('completed')
  })
})

describe('workflow decisions', () => {
  it('preserves explicit review-limit actions and preview forms', () => {
    setActivePinia(createPinia())
    const runtime = useRuntimeStore()
    runtime.addInterruption('s', 'r', { interruptId: 'decision', kind: 'decision', actions: ['accept', 'cancel', 'retry'] })
    runtime.addInterruption('s', 'r', { interruptId: 'preview', kind: 'clarification', previewRequired: true })
    expect(runtime.approvals[0]?.kind).toBe('decision')
    expect(runtime.approvals[0]?.actions).toEqual(['accept', 'cancel', 'retry'])
    expect(runtime.approvals[1]?.previewRequired).toBe(true)
  })
})
