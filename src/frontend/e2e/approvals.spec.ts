import { expect, test, type Page, type WebSocketRoute } from '@playwright/test'

async function runtimeFixture(page: Page, expiresAt: string) {
  const approval = {
    id: 'approval',
    approval_id: 'approval',
    interruptId: 'interrupt',
    runId: 'run',
    kind: 'approval',
    action: '写入 index.html',
    risk: 'write',
    status: 'pending',
    expiresAt,
    resolution: '',
    resolvedAt: '',
  }
  const session = {
    id: 'approval-session',
    projectId: 'project',
    title: '审批流程',
    status: 'waiting',
    runs: [{ id: 'run', status: 'waiting', objective: '修改页面' }],
    tasks: [
      { id: 'task', runId: 'run', status: 'waiting', description: '修改页面', tool: 'write_file' },
    ],
    messages: [
      { id: 'user', runId: 'run', role: 'user', content: '修改页面', status: 'completed' },
    ],
    approvals: [approval],
  }
  let socket!: WebSocketRoute
  let decisionRequestId = ''
  const decisions: Record<string, unknown>[] = []
  const send = (value: unknown) => socket.send(JSON.stringify(value))
  const broadcast = (type: string, payload: Record<string, unknown>) =>
    send({
      protocol_version: 1,
      event_id: crypto.randomUUID(),
      occurred_at: new Date().toISOString(),
      session_id: session.id,
      run_id: 'run',
      type,
      payload,
    })
  await page.routeWebSocket(/ws:\/\/127\.0\.0\.1:.*\/ws/, (connection) => {
    socket = connection
    socket.onMessage((raw) => {
      const request = JSON.parse(String(raw))
      const reply = (result: unknown) =>
        send({ protocol_version: 1, request_id: request.request_id, ok: true, result })
      if (request.method === 'runtime.initialize')
        return reply({ protocolVersion: 1, capabilities: ['approval.resolve'] })
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
        return reply({ agentRuns: [], reviews: [], artifacts: [], events: [], tasks: [] })
      if (request.method === 'approval.resolve') {
        decisions.push(request.params)
        decisionRequestId = request.request_id
        return
      }
      reply({ items: [], files: [] })
    })
  })
  return {
    decisions,
    acknowledge() {
      send({
        protocol_version: 1,
        request_id: decisionRequestId,
        ok: true,
        result: { accepted: true },
      })
    },
    finish(resolution: 'approved' | 'timeout') {
      approval.status = resolution === 'approved' ? 'approved' : 'expired'
      approval.resolution = resolution
      approval.resolvedAt = new Date().toISOString()
      session.status = resolution === 'approved' ? 'completed' : 'failed'
      session.runs[0]!.status = session.status
      session.tasks[0]!.status = session.status
      broadcast('approval.resolved', {
        approval_id: 'approval',
        interrupt_id: 'interrupt',
        approved: resolution === 'approved',
        resolution,
        resolved_at: approval.resolvedAt,
      })
      broadcast(`run.${session.status}`, {
        sessionId: session.id,
        runId: 'run',
        status: session.status,
      })
    },
  }
}

test('disables an expired gate and waits for the explicit timeout result', async ({ page }) => {
  const time = new Date('2026-11-06T10:00:00Z')
  await page.clock.install({ time })
  const runtime = await runtimeFixture(page, new Date(time.getTime() + 60_000).toISOString())
  const errors: string[] = []
  page.on('pageerror', (error) => errors.push(error.message))
  await page.goto('/#/sessions/approval-session')
  const card = page.locator('.approval-card')
  await expect(card.getByRole('button', { name: '允许', exact: true })).toBeEnabled()
  await expect(card.getByRole('timer')).toContainText('秒后自动拒绝')
  await page.clock.fastForward(60_000)
  await expect(card.getByRole('button', { name: '允许', exact: true })).toBeDisabled()
  await expect(card.getByRole('button', { name: '拒绝', exact: true })).toBeDisabled()
  await expect(card.getByRole('status')).toHaveText('期限已到，等待运行时确认')
  expect(runtime.decisions).toHaveLength(0)
  runtime.finish('timeout')
  await expect(card.getByRole('status')).toHaveText('审批已超时，操作已拒绝')
  await expect(card.getByRole('button')).toHaveCount(0)
  await expect(page.getByRole('textbox', { name: '任务内容' })).toBeEnabled()
  await page.reload()
  await expect(page.locator('.approval-card').getByRole('status')).toHaveText(
    '审批已超时，操作已拒绝',
  )
  expect(errors).toEqual([])
  await page.screenshot({ path: '/tmp/aurora-approval-timeout.png', animations: 'disabled' })
})

test('keeps a submitted gate until its resolution and preserves approval after reload', async ({
  page,
}) => {
  const runtime = await runtimeFixture(page, new Date(Date.now() + 60_000).toISOString())
  await page.goto('/#/sessions/approval-session')
  const card = page.locator('.approval-card')
  await card.getByRole('button', { name: '允许', exact: true }).click()
  await expect(card.getByRole('button', { name: '允许', exact: true })).toBeDisabled()
  await expect(card.getByRole('status')).toHaveText('正在提交，等待运行时确认')
  await expect.poll(() => runtime.decisions.length).toBe(1)
  expect(runtime.decisions[0]).toEqual({
    session_id: 'approval-session',
    run_id: 'run',
    approval_id: 'approval',
    approved: true,
  })
  runtime.acknowledge()
  await expect(card.getByRole('status')).toHaveText('正在提交，等待运行时确认')
  runtime.finish('approved')
  await expect(card.getByRole('status')).toHaveText('已允许此操作')
  await expect(card.getByRole('button')).toHaveCount(0)
  await page.reload()
  await expect(page.locator('.approval-card').getByRole('status')).toHaveText('已允许此操作')
})
