import { expect, test, type Page, type WebSocketRoute } from '@playwright/test'

async function retryRuntime(page: Page) {
  const source = { id: 'source', status: 'failed', objective: '修复项目', createdAt: '2026-01-01' }
  const tasks = [
    {
      id: 'completed',
      runId: 'source',
      description: '已完成任务',
      status: 'completed',
      tool: 'write_file',
    },
    {
      id: 'failed',
      runId: 'source',
      description: '失败任务',
      status: 'failed',
      tool: 'run_python',
    },
  ]
  const session = {
    id: 'retry-session',
    projectId: 'project',
    title: '重试流程',
    status: 'failed',
    runs: [source] as Record<string, unknown>[],
    tasks: tasks as Record<string, unknown>[],
    messages: [
      { id: 'user', runId: 'source', role: 'user', content: '修复项目', status: 'completed' },
      {
        id: 'error',
        runId: 'source',
        role: 'assistant',
        content: '原运行失败',
        status: 'failed',
        kind: 'error',
      },
    ],
    approvals: [] as Record<string, unknown>[],
  }
  let socket!: WebSocketRoute
  const calls: Record<string, unknown>[] = []
  const send = (value: unknown) => socket.send(JSON.stringify(value))
  const event = (type: string, payload: Record<string, unknown>) =>
    send({
      protocol_version: 1,
      event_id: crypto.randomUUID(),
      type,
      occurred_at: new Date().toISOString(),
      session_id: session.id,
      run_id: 'retried',
      payload,
    })
  await page.routeWebSocket(/ws:\/\/127\.0\.0\.1:.*\/ws/, (connection) => {
    socket = connection
    socket.onMessage((raw) => {
      const request = JSON.parse(String(raw))
      const reply = (result: unknown) =>
        send({ protocol_version: 1, request_id: request.request_id, ok: true, result })
      if (request.method === 'runtime.initialize')
        return reply({ protocolVersion: 1, capabilities: ['run.retry', 'approval.resolve'] })
      if (request.method === 'project.list')
        return reply({
          items: [
            {
              id: 'project',
              name: '项目',
              path: '/workspace',
              writable: true,
              isGitRepository: true,
            },
          ],
        })
      if (request.method === 'session.list') return reply({ sessions: [session] })
      if (request.method === 'session.get') return reply(session)
      if (request.method === 'run.get')
        return reply({ agentRuns: [], reviews: [], artifacts: [], tasks: [], events: [] })
      if (request.method === 'run.retry') {
        calls.push(request.params)
        const run = {
          id: 'retried',
          session_id: session.id,
          objective: '重试失败任务',
          status: 'running',
          retry_of_run_id: 'source',
          retry_task_id: request.params.task_id ?? null,
          created_at: new Date().toISOString(),
        }
        session.runs.push(run)
        reply({ run })
        run.status = 'waiting'
        session.status = 'waiting'
        session.tasks.push({
          id: 'new-task',
          runId: 'retried',
          description: '失败任务',
          status: 'waiting',
          tool: 'run_python',
          retryOfTaskId: 'failed',
        })
        session.messages.push({
          id: 'retry-user',
          runId: 'retried',
          role: 'user',
          content: '重试失败任务',
          status: 'completed',
        })
        session.approvals = [
          {
            id: 'approval',
            approval_id: 'approval',
            interruptId: 'interrupt',
            runId: 'retried',
            kind: 'approval',
            status: 'pending',
            action: '运行 Python',
            risk: 'execute',
            expiresAt: new Date(Date.now() + 60_000).toISOString(),
          },
        ]
        event('run.started', {
          goal: run.objective,
          retry_of_run_id: 'source',
          retry_task_id: run.retry_task_id,
        })
        event('approval.requested', {
          ...session.approvals[0],
          expires_at: session.approvals[0]!.expiresAt,
        })
        return
      }
      if (request.method === 'approval.resolve') {
        session.approvals[0]!.status = 'approved'
        session.approvals[0]!.resolution = 'approved'
        session.approvals[0]!.resolvedAt = new Date().toISOString()
        session.runs[1]!.status = 'completed'
        session.tasks[2]!.status = 'completed'
        session.status = 'completed'
        reply({ accepted: true })
        event('approval.resolved', {
          approval_id: 'approval',
          interrupt_id: 'interrupt',
          approved: true,
          resolution: 'approved',
          resolved_at: session.approvals[0]!.resolvedAt,
        })
        event('run.completed', {
          sessionId: session.id,
          runId: 'retried',
          status: 'completed',
          retry_of_run_id: 'source',
        })
        return
      }
      reply({ items: [], files: [] })
    })
  })
  return { calls, source, tasks }
}

test('creates a retry with its source visible and requires a fresh approval', async ({ page }) => {
  const runtime = await retryRuntime(page)
  const errors: string[] = []
  page.on('pageerror', (error) => errors.push(error.message))
  await page.goto('/#/sessions/retry-session')
  const source = page.locator('[data-run-id="source"]')
  await source.getByRole('button', { name: '重试运行', exact: true }).click()
  const retried = page.locator('[data-run-id="retried"]')
  await expect(retried.getByText('未完成任务重试', { exact: true })).toBeVisible()
  await expect(retried.getByRole('button', { name: '查看来源', exact: true })).toBeVisible()
  await expect(retried.getByRole('button', { name: '允许', exact: true })).toBeEnabled()
  await expect(source.getByRole('button', { name: '重试运行', exact: true })).toBeDisabled()
  expect(runtime.calls).toEqual([{ session_id: 'retry-session', run_id: 'source' }])
  expect(runtime.source.status).toBe('failed')
  expect(runtime.tasks[0]?.status).toBe('completed')
  await retried.getByRole('button', { name: '允许', exact: true }).click()
  await expect(retried.getByRole('status')).toHaveText('已允许此操作')
  await expect(page.getByRole('textbox', { name: '任务内容' })).toBeEnabled()
  await expect(source.getByText('原运行失败', { exact: true })).toBeVisible()
  await page.reload()
  await expect(
    page.locator('[data-run-id="retried"]').getByText('未完成任务重试', { exact: true }),
  ).toBeVisible()
  await expect(page.locator('[data-run-id="retried"]').getByRole('status')).toBeInViewport()
  await page
    .locator('[data-run-id="retried"]')
    .getByRole('button', { name: '查看来源', exact: true })
    .click()
  await expect(
    page.locator('[data-run-id="source"]').getByText('原运行失败', { exact: true }),
  ).toBeInViewport()
  await page.locator('main.conversation-scroll').evaluate((element) => {
    element.scrollTop = element.scrollHeight
  })
  expect(errors).toEqual([])
  await page.screenshot({ path: '/tmp/aurora-retry-history.png', animations: 'disabled' })
})

test('retries only the task selected in its detail drawer', async ({ page }) => {
  const runtime = await retryRuntime(page)
  await page.goto('/#/sessions/retry-session')
  await page
    .locator('.task-tree-panel')
    .getByRole('button', { name: /失败任务/ })
    .click()
  await page.getByRole('button', { name: '重试此任务', exact: true }).click()
  await expect(
    page.locator('[data-run-id="retried"]').getByText('任务重试', { exact: true }),
  ).toBeVisible()
  expect(runtime.calls).toEqual([
    { session_id: 'retry-session', run_id: 'source', task_id: 'failed' },
  ])
  expect(runtime.source.status).toBe('failed')
})
