import { test, expect } from '@playwright/test'

test('shows the failed prerequisite and retries the blocked task by its persistent ID', async ({
  page,
}) => {
  const source = { id: 'run', status: 'failed', objective: '准备并发布', createdAt: '2026-01-01' }
  const session = {
    id: 'session',
    title: '发布流程',
    projectId: 'project',
    status: 'failed',
    runs: [source],
    tasks: [
      { id: 'prepare-uuid', runId: 'run', description: '准备文件', status: 'failed' },
      { id: 'inspect-uuid', runId: 'run', description: '独立检查', status: 'completed' },
      {
        id: 'publish-uuid',
        runId: 'run',
        description: '发布文件',
        status: 'blocked',
        dependsOn: ['prepare-uuid', 'inspect-uuid'],
        blockedBy: ['prepare-uuid'],
      },
    ],
    messages: [],
    approvals: [],
  }
  const retries: Record<string, unknown>[] = []
  await page.routeWebSocket(/ws:\/\/127\.0\.0\.1:.*\/ws/, (socket) => {
    socket.onMessage((raw) => {
      const request = JSON.parse(String(raw))
      const reply = (result: unknown) =>
        socket.send(
          JSON.stringify({ protocol_version: 1, request_id: request.request_id, ok: true, result }),
        )
      if (request.method === 'runtime.initialize')
        return reply({ protocolVersion: 1, capabilities: ['run.retry'] })
      if (request.method === 'project.list')
        return reply({
          items: [{ id: 'project', name: '项目', path: '/workspace', writable: true }],
        })
      if (request.method === 'session.list') return reply({ sessions: [session] })
      if (request.method === 'session.get') return reply(session)
      if (request.method === 'run.retry') {
        retries.push(request.params)
        return reply({
          run: {
            id: 'retried',
            session_id: 'session',
            status: 'running',
            retry_of_run_id: 'run',
            retry_task_id: 'publish-uuid',
          },
        })
      }
      return reply({ items: [], files: [], agentRuns: [], reviews: [], artifacts: [] })
    })
  })
  await page.goto('/#/sessions/session')
  await page
    .locator('[data-task-id="publish-uuid"]')
    .getByRole('button', { name: /发布文件/ })
    .click()
  const drawer = page.locator('.n-drawer')
  await expect(drawer.getByText('已阻断', { exact: true })).toBeVisible()
  await expect(drawer.getByText('准备文件（未通过）、独立检查', { exact: true })).toBeVisible()
  await drawer.getByRole('button', { name: '重试前置任务并继续' }).click()
  await expect
    .poll(() => retries)
    .toEqual([{ session_id: 'session', run_id: 'run', task_id: 'publish-uuid' }])
})
