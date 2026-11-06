import { beforeEach, describe, expect, it, vi } from 'vitest'
import { createPinia, setActivePinia } from 'pinia'
import { useRuntimeStore } from '~/stores/runtimeStore'
import { useSessionStore } from '~/stores/sessionStore'
import { runtimeRequest } from '~/utils/runtimeClient'
import type { RuntimeEvent } from '~/types/agent'

vi.mock('~/utils/runtimeClient', () => ({ runtimeRequest: vi.fn() }))

function event(type: string, payload: Record<string, unknown>): RuntimeEvent {
  return {
    protocol_version: 1,
    event_id: crypto.randomUUID(),
    occurred_at: '',
    session_id: 's',
    run_id: 'r',
    type,
    payload,
  }
}

function setup() {
  const sessions = useSessionStore()
  sessions.upsert({ id: 's', status: 'waiting', runs: [{ id: 'r', status: 'waiting' }] })
  vi.spyOn(sessions, 'load').mockImplementation(async () => sessions.getSession('s')!)
  const runtime = useRuntimeStore()
  runtime.addInterruption('s', 'r', {
    interruptId: 'i',
    approval_id: 'a',
    kind: 'approval',
    tool: 'write_file',
    expires_at: '2026-11-06T12:00:00Z',
  })
  return { runtime, sessions }
}

describe('approval confirmations', () => {
  beforeEach(() => {
    setActivePinia(createPinia())
    vi.mocked(runtimeRequest).mockReset()
  })

  it('waits for a persisted result instead of accepting an acknowledgement as approval', async () => {
    const { runtime, sessions } = setup()
    vi.mocked(runtimeRequest).mockResolvedValue({ accepted: true })
    await runtime.resolveApproval('i', true)
    expect(runtimeRequest).toHaveBeenCalledWith('approval.resolve', {
      session_id: 's',
      run_id: 'r',
      approval_id: 'a',
      approved: true,
    })
    expect(runtime.approvalsFor('s')).toHaveLength(1)
    expect(sessions.getSession('s')?.status).toBe('waiting')
    await runtime.resolveApproval('i', true)
    expect(runtimeRequest).toHaveBeenCalledTimes(1)
    await runtime.handleEvent(
      event('approval.resolved', {
        approval_id: 'a',
        interrupt_id: 'i',
        approved: true,
        resolution: 'approved',
        resolved_at: '2026-11-06T10:00:00Z',
      }),
    )
    expect(runtime.approvals[0]?.status).toBe('approved')
    expect(runtime.pendingInputs.i).toBeUndefined()
    expect(runtime.requestsFor('s')).toHaveLength(1)
  })

  it('keeps timeout explicit even if the run terminal arrives first', async () => {
    const { runtime } = setup()
    await runtime.handleEvent(event('run.failed', { sessionId: 's', runId: 'r', status: 'failed' }))
    await runtime.handleEvent(
      event('approval.resolved', {
        approval_id: 'a',
        interrupt_id: 'i',
        approved: false,
        resolution: 'timeout',
        resolved_at: '2026-11-06T12:00:00Z',
      }),
    )
    expect(runtime.approvals[0]?.resolution).toBe('timeout')
    expect(runtime.approvals[0]?.status).toBe('expired')
    expect(runtime.approvalsFor('s')).toHaveLength(0)
  })

  it('does not reopen a resolved gate when requested events arrive late or twice', async () => {
    const { runtime } = setup()
    await runtime.handleEvent(
      event('approval.resolved', {
        approval_id: 'a',
        approved: false,
        resolution: 'timeout',
        resolved_at: '2026-11-06T12:00:00Z',
      }),
    )
    await runtime.handleEvent(
      event('approval.requested', {
        approval_id: 'a',
        interruptId: 'i',
        expires_at: '2026-11-06T12:00:00Z',
      }),
    )
    await runtime.handleEvent(event('approval.required', { approval_id: 'a', interruptId: 'i' }))
    expect(runtime.approvals).toHaveLength(1)
    expect(runtime.approvals[0]?.status).toBe('expired')
    expect(runtime.approvalsFor('s')).toHaveLength(0)
  })

  it('preserves an approval result through terminal session reloads and stale snapshots', async () => {
    const { runtime, sessions } = setup()
    await runtime.handleEvent(
      event('approval.resolved', {
        approval_id: 'a',
        approved: true,
        resolution: 'approved',
        resolved_at: '2026-11-06T10:00:00Z',
      }),
    )
    const history = sessions.upsert({
      id: 's',
      status: 'completed',
      runs: [{ id: 'r', status: 'completed' }],
      approvals: [
        {
          id: 'a',
          approval_id: 'a',
          interruptId: 'i',
          runId: 'r',
          kind: 'approval',
          status: 'approved',
          resolution: 'approved',
          resolvedAt: '2026-11-06T10:00:00Z',
        },
      ],
    })
    runtime.syncApprovals(history)
    expect(history.approvals[0]?.status).toBe('approved')
    runtime.syncApprovals({
      ...history,
      approvals: [
        { ...history.approvals[0]!, status: 'pending', resolvedAt: null, resolution: null },
      ],
    })
    expect(runtime.approvals[0]?.status).toBe('approved')
  })

  it('restores authoritative expired history when a late submission is rejected', async () => {
    const { runtime, sessions } = setup()
    vi.mocked(runtimeRequest).mockRejectedValue(new Error('审批已过期'))
    vi.mocked(sessions.load).mockImplementation(async () =>
      sessions.upsert({
        id: 's',
        status: 'failed',
        runs: [{ id: 'r', status: 'failed' }],
        approvals: [
          {
            id: 'a',
            approval_id: 'a',
            interruptId: 'i',
            runId: 'r',
            kind: 'approval',
            status: 'expired',
            resolution: 'timeout',
            resolvedAt: '2026-11-06T12:00:00Z',
          },
        ],
      }),
    )
    await expect(runtime.resolveApproval('i', true)).rejects.toThrow('过期')
    expect(runtime.approvals[0]?.resolution).toBe('timeout')
    expect(runtime.pendingInputs.i).toBeUndefined()
    expect(sessions.getSession('s')?.status).toBe('failed')
  })

  it('does not replace a resolved result with a late transport error', async () => {
    const { runtime } = setup()
    let reject!: (error: Error) => void
    vi.mocked(runtimeRequest).mockImplementation(
      () =>
        new Promise((_, fail) => {
          reject = fail
        }),
    )
    const submitting = runtime.resolveApproval('i', false)
    await runtime.handleEvent(
      event('approval.resolved', {
        approval_id: 'a',
        approved: false,
        resolution: 'rejected',
        resolved_at: '2026-11-06T10:00:00Z',
      }),
    )
    reject(new Error('传输已断开'))
    await expect(submitting).rejects.toThrow('断开')
    expect(runtime.approvals[0]?.status).toBe('rejected')
    expect(runtime.pendingInputs.i).toBeUndefined()
  })
})
