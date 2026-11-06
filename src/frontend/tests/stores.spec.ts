import { beforeEach, describe, expect, it, vi } from 'vitest'
import { createPinia, setActivePinia } from 'pinia'
import { useProjectStore } from '~/stores/projectStore'
import { useGitStore } from '~/stores/gitStore'
import { useSessionStore } from '~/stores/sessionStore'
import { useUiStore } from '~/stores/uiStore'
import { runtimeRequest } from '~/utils/runtimeClient'

vi.mock('~/utils/runtimeClient', () => ({ runtimeRequest: vi.fn() }))

describe('application stores', () => {
  beforeEach(() => {
    setActivePinia(createPinia())
    const values = new Map<string, string>()
    vi.stubGlobal('localStorage', {
      getItem: (key: string) => values.get(key) ?? null,
      setItem: (key: string, value: string) => values.set(key, value),
      removeItem: (key: string) => values.delete(key),
      clear: () => values.clear(),
    })
    vi.mocked(runtimeRequest).mockReset()
  })

  it('creates and reuses validated workspaces', async () => {
    vi.mocked(runtimeRequest).mockResolvedValue({
      id: 'p1', name: 'workspace', path: '/tmp/workspace', isGitRepository: true, writable: true,
    })
    const projects = useProjectStore()
    const first = await projects.create('/tmp/workspace')
    const second = await projects.create('/tmp/workspace')
    expect(first.id).toBe(second.id)
    expect(projects.projects).toHaveLength(1)
    expect(projects.activeProjectId).toBe(first.id)
  })

  it('automatically initializes a non-Git workspace', async () => {
    vi.mocked(runtimeRequest)
      .mockResolvedValueOnce({ name: 'workspace', path: '/tmp/workspace', isGitRepository: false, writable: true })
      .mockResolvedValueOnce({ name: 'workspace', path: '/tmp/workspace', isGitRepository: true, writable: true })
      .mockResolvedValueOnce({ id: 'p1', name: 'workspace', path: '/tmp/workspace' })
    const project = await useProjectStore().create('/tmp/workspace')
    expect(project.isGitRepository).toBe(true)
    expect(runtimeRequest).toHaveBeenNthCalledWith(2, 'workspace.git.initialize', { path: '/tmp/workspace' })
  })

  it('loads Git status and rolls back a run', async () => {
    const status = {
      view: 'run', runId: 'r1', branch: 'main', head: 'abc', unborn: false,
      files: [{ path: 'a.ts', status: 'M', staged: false, unstaged: true, untracked: false, binary: false, additions: 1, deletions: 1 }],
      canRollback: true, rollbackReason: '',
    }
    vi.mocked(runtimeRequest).mockResolvedValueOnce(status).mockResolvedValueOnce({ reverted: true })
    const git = useGitStore()
    await git.loadStatus('s1', 'run', 'r1')
    expect(git.statusFor('s1', 'run', 'r1')?.files[0]?.path).toBe('a.ts')
    await git.rollback('s1', 'r1')
    expect(git.statusFor('s1', 'run', 'r1')).toBeUndefined()
  })

  it('creates and removes a session bound to the active project', async () => {
    const projects = useProjectStore()
    projects.projects.push({
      id: 'p1', name: 'workspace', path: '/tmp/workspace', isGitRepository: true,
      writable: true, createdAt: '2026-01-01', updatedAt: '2026-01-01',
    })
    projects.activeProjectId = 'p1'
    vi.mocked(runtimeRequest).mockResolvedValue({ sessionId: 's1', projectId: 'p1', workflowId: 'w1' })
    const sessions = useSessionStore()
    const session = await sessions.create('测试')
    expect(session.projectId).toBe('p1')
    await sessions.delete('s1')
    expect(sessions.sessions).toHaveLength(0)
  })

  it('updates UI preferences', () => {
    const ui = useUiStore()
    ui.setTheme('dark')
    ui.toggleSidebar()
    expect(ui.theme).toBe('dark')
    expect(ui.sidebarCollapsed).toBe(true)
  })
})

describe('persistent session history', () => {
  beforeEach(() => { setActivePinia(createPinia()); vi.mocked(runtimeRequest).mockReset() })

  it('reloads interrupted sessions and keeps internal agent attribution', async () => {
    vi.mocked(runtimeRequest).mockResolvedValueOnce({ sessions: [{ id: 's1', title: '历史', status: 'interrupted' }] })
      .mockResolvedValueOnce({ id: 's1', title: '历史', workflowId: 'flow', status: 'interrupted', messages: [
        { id: 'm1', seq: 2, role: 'assistant', content: '审查意见', agentRunId: 'agent-run', visibility: 'internal' },
      ] })
    const sessions = useSessionStore()
    await sessions.loadAll()
    expect(sessions.getSession('s1')?.status).toBe('interrupted')
    await sessions.load('s1')
    expect(sessions.getSession('s1')?.messages[0]?.agentRunId).toBe('agent-run')
    expect(sessions.getSession('s1')?.messages[0]?.visibility).toBe('internal')
    expect(sessions.getSession('s1')?.workflowId).toBe('flow')
  })

  it('paginates older messages without duplication', async () => {
    const sessions = useSessionStore()
    sessions.upsert({ id: 's', nextBeforeSeq: 3, messages: [{ id: 'm3', seq: 3, role: 'user', content: 'three' }] })
    vi.mocked(runtimeRequest).mockResolvedValue({ messages: [
      { id: 'm1', seq: 1, role: 'user', content: 'one' }, { id: 'm3', seq: 3, role: 'user', content: 'three' },
    ], nextBeforeSeq: null })
    await sessions.loadOlder('s')
    expect(sessions.getSession('s')?.messages.map((item) => item.id)).toEqual(['m1', 'm3'])
    expect(sessions.getSession('s')?.nextBeforeSeq).toBeNull()
  })

  it('does not remove a session when the backend refuses deletion', async () => {
    const sessions = useSessionStore()
    sessions.upsert({ id: 's', title: 'running', status: 'running' })
    vi.mocked(runtimeRequest).mockRejectedValue(new Error('会话已有活动运行'))
    await expect(sessions.delete('s')).rejects.toThrow('活动运行')
    expect(sessions.getSession('s')).toBeDefined()
  })
})

describe('approval responsiveness', () => {
  beforeEach(() => { setActivePinia(createPinia()); vi.mocked(runtimeRequest).mockReset() })

  it('keeps the gate pending while submitting, blocks duplicates and allows failed submission retries', async () => {
    const { useRuntimeStore } = await import('~/stores/runtimeStore')
    const runtime = useRuntimeStore()
    const sessions = useSessionStore()
    sessions.upsert({ id: 's', status: 'waiting', messages: [], tasks: [], runs: [{ id: 'r', status: 'waiting' }] })
    runtime.addInterruption('s', 'r', { interruptId: 'confirm' })
    let rejectRequest!: (error: Error) => void
    vi.mocked(runtimeRequest).mockImplementationOnce(() => new Promise((_resolve, reject) => { rejectRequest = reject }))
    const submission = runtime.resolveApproval('confirm', true)
    expect(runtime.approvalsFor('s')).toHaveLength(1)
    expect(runtime.pendingInputs.confirm).toBeDefined()
    expect(sessions.getSession('s')?.status).toBe('waiting')
    await runtime.resolveApproval('confirm', true)
    expect(runtimeRequest).toHaveBeenCalledTimes(1)
    rejectRequest(new Error('提交失败'))
    await expect(submission).rejects.toThrow('提交失败')
    expect(runtime.approvalsFor('s')).toHaveLength(1)
    expect(sessions.getSession('s')?.status).toBe('waiting')
  })

  it('does not reopen an accepted gate when subsequent execution fails', async () => {
    const { useRuntimeStore } = await import('~/stores/runtimeStore')
    const runtime = useRuntimeStore()
    useSessionStore().upsert({ id: 's', status: 'waiting', messages: [], tasks: [], runs: [] })
    runtime.addInterruption('s', 'r', { interruptId: 'confirm' })
    let rejectRequest!: (error: Error) => void
    vi.mocked(runtimeRequest).mockImplementationOnce(() => new Promise((_resolve, reject) => { rejectRequest = reject }))
    const submission = runtime.resolveApproval('confirm', true)
    await runtime.handleEvent({ protocol_version: 1, event_id: 'resolved', type: 'approval.resolved', occurred_at: '', session_id: 's', run_id: 'r', payload: { interrupt_id: 'confirm', approved: true, resolution: 'approved', resolved_at: '2026-11-06T00:00:00Z' } })
    await runtime.handleEvent({ protocol_version: 1, event_id: 'resumed', type: 'run.resumed', occurred_at: '', session_id: 's', run_id: 'r', payload: {} })
    rejectRequest(new Error('执行失败'))
    await expect(submission).rejects.toThrow('执行失败')
    expect(runtime.approvalsFor('s')).toHaveLength(0)
  })
})
