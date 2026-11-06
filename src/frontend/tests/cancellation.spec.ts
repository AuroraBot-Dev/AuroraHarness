import { beforeEach, describe, expect, it, vi } from 'vitest'
import { createPinia, setActivePinia } from 'pinia'
import { useRuntimeStore } from '~/stores/runtimeStore'
import { useSessionStore } from '~/stores/sessionStore'
import { runtimeRequest } from '~/utils/runtimeClient'
import type { RuntimeEvent } from '~/types/agent'

vi.mock('~/utils/runtimeClient', () => ({ runtimeRequest: vi.fn() }))

function event(type: string, payload: Record<string, unknown> = {}): RuntimeEvent {
  return {
    protocol_version: 1,
    event_id: crypto.randomUUID(),
    occurred_at: '2026-11-06',
    session_id: 's',
    run_id: 'r',
    type,
    payload,
  }
}

function setup() {
  const sessions = useSessionStore()
  const session = sessions.upsert({
    id: 's',
    status: 'running',
    runs: [{ id: 'r', status: 'running' }],
    tasks: [
      { id: 'done', runId: 'r', status: 'completed' },
      { id: 'pending', runId: 'r', status: 'running' },
    ],
  })
  const runtime = useRuntimeStore()
  runtime.connectionStatus = 'connected'
  vi.spyOn(sessions, 'load').mockImplementation(async () => sessions.getSession('s')!)
  return { sessions, session, runtime }
}

describe('cooperative run cancellation', () => {
  beforeEach(() => {
    setActivePinia(createPinia())
    vi.mocked(runtimeRequest).mockReset()
  })

  it('keeps running until the backend confirms cancellation and deduplicates stop requests', async () => {
    const { session, runtime } = setup()
    vi.mocked(runtimeRequest).mockResolvedValue({ accepted: true })
    await runtime.cancelRun('s')
    await runtime.cancelRun('s')
    expect(runtimeRequest).toHaveBeenCalledExactlyOnceWith('run.cancel', {
      session_id: 's',
      run_id: 'r',
    })
    expect(session.status).toBe('running')
    expect(runtime.pendingCancellations.r).toBe(true)
    await runtime.handleEvent(event('run.cancelled', { status: 'cancelled' }))
    expect(session.status).toBe('cancelled')
    expect(runtime.pendingCancellations.r).toBeUndefined()
    expect(session.tasks.map((task) => task.status)).toEqual(['completed', 'cancelled'])
  })

  it('expires approvals and ignores late active events and response snapshots', async () => {
    const { session, runtime } = setup()
    runtime.addInterruption('s', 'r', { interruptId: 'approval' })
    await runtime.handleEvent(event('run.cancelled', { status: 'cancelled' }))
    await runtime.handleEvent(event('run.resumed'))
    await runtime.handleEvent(event('task.started', { taskId: 'pending' }))
    await runtime.handleEvent(event('message.delta', { messageId: 'late', delta: 'late' }))
    await runtime.handleEvent(event('approval.required', { interruptId: 'late' }))
    runtime.applyUpdate({
      sessionId: 's',
      runId: 'r',
      status: 'waiting',
      interruptions: [{ interruptId: 'later' }],
    })
    expect(session.status).toBe('cancelled')
    expect(session.tasks[1]?.status).toBe('cancelled')
    expect(session.messages).toHaveLength(0)
    expect(runtime.approvalsFor('s')).toHaveLength(0)
    expect(runtime.approvals[0]?.status).toBe('expired')
  })

  it('preserves cancellation across a stale session reload', async () => {
    const { sessions, runtime } = setup()
    await runtime.handleEvent(event('run.cancelled', { status: 'cancelled' }))
    sessions.upsert({
      id: 's',
      status: 'waiting',
      runs: [{ id: 'r', status: 'waiting' }],
      tasks: [{ id: 'pending', runId: 'r', status: 'waiting' }],
      approvals: [{ id: 'old', runId: 'r', status: 'pending' }],
    })
    const loaded = sessions.getSession('s')!
    expect(loaded.status).toBe('cancelled')
    expect(loaded.tasks.find((task) => task.id === 'pending')?.status).toBe('cancelled')
    expect(loaded.approvals[0]?.status).toBe('expired')
  })

  it('does not turn cancellation into failure when the original request rejects late', async () => {
    const { session, runtime } = setup()
    session.runs = []
    let rejectStart!: (error: Error) => void
    vi.mocked(runtimeRequest).mockImplementation(
      () =>
        new Promise((_, reject) => {
          rejectStart = reject
        }),
    )
    const start = runtime.startRun('s', '执行')
    await runtime.handleEvent(event('run.started', { goal: '执行' }))
    expect(runtime.activeRunId('s')).toBe('r')
    await runtime.handleEvent(event('run.cancelled', { status: 'cancelled' }))
    rejectStart(new Error('迟到的传输错误'))
    expect((await start).status).toBe('cancelled')
    expect(session.status).toBe('cancelled')
    expect(session.messages.some((message) => message.kind === 'error')).toBe(false)
  })

  it('does not restore a cancelled approval when resume rejects late', async () => {
    const { session, runtime } = setup()
    runtime.addInterruption('s', 'r', { interruptId: 'approval' })
    let rejectResume!: (error: Error) => void
    vi.mocked(runtimeRequest).mockImplementation(
      () =>
        new Promise((_, reject) => {
          rejectResume = reject
        }),
    )
    const resume = runtime.resumeInput('approval', { approved: true })
    await runtime.handleEvent(event('run.cancelled', { status: 'cancelled' }))
    rejectResume(new Error('审批已过期'))
    await resume
    expect(session.status).toBe('cancelled')
    expect(runtime.approvalsFor('s')).toHaveLength(0)
  })

  it('keeps a newer run active when a cancelled request rejects late', async () => {
    const { session, runtime } = setup()
    session.runs = []
    let rejectOld!: (error: Error) => void
    let resolveNew!: (value: unknown) => void
    vi.mocked(runtimeRequest)
      .mockImplementationOnce(
        () =>
          new Promise((_, reject) => {
            rejectOld = reject
          }),
      )
      .mockImplementationOnce(
        () =>
          new Promise((resolve) => {
            resolveNew = resolve
          }),
      )
    const first = runtime.startRun('s', '第一轮')
    await runtime.handleEvent(event('run.started'))
    await runtime.handleEvent(event('run.cancelled', { status: 'cancelled' }))
    const second = runtime.startRun('s', '第二轮')
    await runtime.handleEvent({ ...event('run.started'), run_id: 'new-run' })
    rejectOld(new Error('旧请求已断开'))
    expect((await first).status).toBe('cancelled')
    expect(session.status).toBe('running')
    expect(runtime.activeRunId('s')).toBe('new-run')
    resolveNew({ sessionId: 's', runId: 'new-run', status: 'completed' })
    await second
    expect(session.status).toBe('completed')
  })

  it('allows retrying stop after a transport error without changing run status', async () => {
    const { session, runtime } = setup()
    vi.mocked(runtimeRequest)
      .mockRejectedValueOnce(new Error('连接中断'))
      .mockResolvedValueOnce({ accepted: false })
    await expect(runtime.cancelRun('s')).rejects.toThrow('连接中断')
    expect(runtime.pendingCancellations.r).toBeUndefined()
    await runtime.cancelRun('s')
    expect(runtimeRequest).toHaveBeenCalledTimes(2)
    expect(session.status).toBe('running')
    expect(runtime.pendingCancellations.r).toBeUndefined()
  })

  it.each([true, false])('refreshes an already ended run when accepted is %s', async (accepted) => {
    const { sessions, runtime } = setup()
    runtime.addInterruption('s', 'r', { interruptId: 'approval' })
    vi.mocked(runtimeRequest).mockResolvedValue({ accepted })
    vi.mocked(sessions.load).mockImplementation(async () =>
      sessions.upsert({
        id: 's',
        status: 'cancelled',
        runs: [{ id: 'r', status: 'cancelled' }],
      }),
    )
    await runtime.cancelRun('s')
    expect(sessions.getSession('s')?.status).toBe('cancelled')
    expect(runtime.pendingCancellations.r).toBeUndefined()
    expect(runtime.approvalsFor('s')).toHaveLength(0)
  })
})
