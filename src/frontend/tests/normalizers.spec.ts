import { describe, expect, it } from 'vitest'
import { normalizeSession } from '~/utils/normalizers'

describe('backend cache normalization', () => {
  it('flattens recursively related tasks from every run', () => {
    const session = normalizeSession({ id: 's1', title: '任务', created_at: '2026-01-01', updated_at: '2026-01-01', runs: [{
      id: 'r1', session_id: 's1', objective: '目标', status: 'running', created_at: '2026-01-01', updated_at: '2026-01-01',
      tasks: [
        { id: 'root', parent_id: null, description: '根', effort: 'high', status: 'running' },
        { id: 'child', parent_id: 'root', description: '子', effort: 'low', status: 'queued' },
      ],
    }] })
    expect(session.status).toBe('running')
    expect(session.tasks.find((task) => task.id === 'child')?.parentId).toBe('root')
  })

  it('maps snake_case attachment fields without exposing extra values', () => {
    const session = normalizeSession({ id: 's1', title: '附件', messages: [{ id: 'm1', session_id: 's1', role: 'user', content: '看文件', attachments: [{ id: 'a1', name: 'a.md', path: 'C:/a.md', mime_type: 'text/markdown', size: 3 }] }] })
    expect(session.messages[0]?.attachments[0]).toMatchObject({ name: 'a.md', mediaType: 'text/markdown', size: 3 })
  })

  it('restores approvals and retry relationships from a session snapshot', () => {
    const session = normalizeSession({
      id: 's1',
      runs: [{ id: 'r2', retry_of_run_id: 'r1', retry_task_id: 't1' }],
      approvals: [{ id: 'approval-1', session_id: 's1', run_id: 'r2', task_id: 't2', status: 'pending', action: '写文件', risk: '修改本地数据' }],
    })
    expect(session.runs[0]).toMatchObject({ retryOfRunId: 'r1', retryTaskId: 't1' })
    expect(session.approvals[0]).toMatchObject({ id: 'approval-1', taskId: 't2', status: 'pending' })
  })
})
