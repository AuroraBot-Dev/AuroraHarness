import { beforeEach, describe, expect, it, vi } from 'vitest'
import { createPinia, setActivePinia } from 'pinia'
import { useRuntimeStore } from '~/stores/runtimeStore'
import { useSessionStore } from '~/stores/sessionStore'
import { runtimeRequest } from '~/utils/runtimeClient'

vi.mock('~/utils/runtimeClient', () => ({ runtimeRequest: vi.fn() }))

function setup() {
  const sessions = useSessionStore()
  const session = sessions.upsert({
    id: 's',
    status: 'failed',
    runs: [{ id: 'original', status: 'failed', objective: '原任务', createdAt: '2026-01-01' }],
  })
  vi.spyOn(sessions, 'load').mockImplementation(async () => sessions.getSession('s')!)
  const runtime = useRuntimeStore()
  runtime.connectionStatus = 'connected'
  return { sessions, session, runtime }
}

const response = (taskId: string | null = null) => ({
  run: {
    id: 'new',
    session_id: 's',
    status: 'running',
    objective: '重试',
    retry_of_run_id: 'original',
    retry_task_id: taskId,
  },
})

describe('run retries', () => {
  beforeEach(() => {
    setActivePinia(createPinia())
    vi.mocked(runtimeRequest).mockReset()
  })

  it('waits for a new backend identity, deduplicates clicks and keeps source history unchanged', async () => {
    const { session, runtime } = setup()
    const before = structuredClone(session.runs[0]!)
    let resolve!: (value: unknown) => void
    vi.mocked(runtimeRequest).mockImplementation(
      () =>
        new Promise((done) => {
          resolve = done
        }),
    )
    const pending = runtime.retryRun('s', 'original')
    await runtime.retryRun('s', 'original')
    expect(runtimeRequest).toHaveBeenCalledTimes(1)
    expect(session.runs).toHaveLength(1)
    expect(session.status).toBe('failed')
    expect(runtime.retryPendingFor('s')).toBe(true)
    resolve(response())
    await pending
    expect(session.runs[0]).toEqual(before)
    expect(session.runs[1]?.retryOfRunId).toBe('original')
    expect(runtime.activeRunId('s')).toBe('new')
    expect(session.status).toBe('running')
    expect(runtime.retryPendingFor('s')).toBe(false)
  })

  it('sends the selected task and records its source on the new run', async () => {
    const { session, runtime } = setup()
    vi.mocked(runtimeRequest).mockResolvedValue(response('task'))
    await runtime.retryRun('s', 'original', 'task')
    expect(runtimeRequest).toHaveBeenCalledExactlyOnceWith('run.retry', {
      session_id: 's',
      run_id: 'original',
      task_id: 'task',
    })
    expect(session.runs[1]?.retryTaskId).toBe('task')
    expect(session.runs[0]?.status).toBe('failed')
  })

  it('does not roll a terminal retry back when its creation acknowledgement arrives late', async () => {
    const { session, runtime } = setup()
    let resolve!: (value: unknown) => void
    vi.mocked(runtimeRequest).mockImplementation(
      () =>
        new Promise((done) => {
          resolve = done
        }),
    )
    const pending = runtime.retryRun('s', 'original')
    await runtime.handleEvent({
      protocol_version: 1,
      event_id: 'done',
      type: 'run.completed',
      occurred_at: '',
      session_id: 's',
      run_id: 'new',
      payload: { sessionId: 's', runId: 'new', status: 'completed', retry_of_run_id: 'original' },
    })
    resolve(response())
    await pending
    expect(session.status).toBe('completed')
    expect(session.runs[1]?.status).toBe('completed')
    expect(runtime.activeRunId('s')).toBe('')
  })

  it('allows another attempt after rejection without rewriting the source', async () => {
    const { session, runtime } = setup()
    vi.mocked(runtimeRequest)
      .mockRejectedValueOnce(new Error('工作流已停用'))
      .mockResolvedValueOnce(response())
    await expect(runtime.retryRun('s', 'original')).rejects.toThrow('停用')
    expect(session.runs).toHaveLength(1)
    expect(session.status).toBe('failed')
    expect(runtime.retryPendingFor('s')).toBe(false)
    await runtime.retryRun('s', 'original')
    expect(session.runs).toHaveLength(2)
  })

  it('keeps the created run controllable when its history refresh fails', async () => {
    const { sessions, runtime } = setup()
    vi.mocked(runtimeRequest).mockResolvedValue(response())
    vi.mocked(sessions.load).mockRejectedValue(new Error('历史读取失败'))
    await expect(runtime.retryRun('s', 'original')).rejects.toThrow('读取失败')
    expect(runtime.activeRunId('s')).toBe('new')
    expect(sessions.isRunning('s')).toBe(true)
    expect(sessions.getSession('s')?.runs[0]?.status).toBe('failed')
  })

  it('discovers a created run after an acknowledgement is lost', async () => {
    const { sessions, runtime } = setup()
    runtime.runRequests.s = { requestKey: 'old-request', runId: 'original' }
    const originalControl = runtime.runRequests.s
    let reject!: (error: Error) => void
    vi.mocked(runtimeRequest).mockImplementation(
      () =>
        new Promise((_, fail) => {
          reject = fail
        }),
    )
    vi.mocked(sessions.load).mockImplementation(async () =>
      sessions.upsert({
        id: 's',
        status: 'running',
        runs: [
          { id: 'original', status: 'failed', createdAt: '2026-01-01' },
          { id: 'new', status: 'running', retry_of_run_id: 'original', createdAt: '2026-11-06' },
        ],
      }),
    )
    const pending = runtime.retryRun('s', 'original')
    await runtime.handleEvent({
      protocol_version: 1,
      event_id: 'new-run',
      occurred_at: '',
      type: 'run.started',
      session_id: 's',
      run_id: 'new',
      payload: { goal: '重试', retry_of_run_id: 'original' },
    })
    expect(originalControl.runId).toBe('original')
    reject(new Error('请求响应丢失'))
    await expect(pending).rejects.toThrow('丢失')
    expect(runtime.activeRunId('s')).toBe('new')
    expect(sessions.getSession('s')?.runs[0]?.status).toBe('failed')
  })

  it.each(['disconnected', 'active'])('blocks retry while %s', async (state) => {
    const { session, runtime } = setup()
    if (state === 'disconnected') runtime.connectionStatus = 'disconnected'
    else session.status = 'running'
    await expect(runtime.retryRun('s', 'original')).rejects.toThrow()
    expect(runtimeRequest).not.toHaveBeenCalled()
  })
})
