import { expect, test, type Page } from '@playwright/test'

async function sessionRuntime(page: Page, status: 'completed' | 'running' | 'waiting') {
  const session = {
    id: 'options-session',
    projectId: 'project',
    title: '会话选项',
    workflowId: 'single',
    status,
    runs: [{ id: 'run', status, objective: '整理项目' }],
    messages: [
      { id: 'user', runId: 'run', role: 'user', content: '整理项目', status: 'completed' },
      {
        id: 'reply',
        runId: 'run',
        role: 'assistant',
        content: '这是保留的历史答复。',
        status: 'completed',
      },
    ],
    tasks: [],
    approvals:
      status === 'waiting'
        ? [
            {
              id: 'approval',
              runId: 'run',
              kind: 'approval',
              status: 'pending',
              action: '写入项目说明',
              risk: 'write',
              expiresAt: new Date(Date.now() + 90_000).toISOString(),
            },
          ]
        : [],
  }
  const changes: { method: string; params: Record<string, unknown> }[] = []
  let reads = 0
  await page.routeWebSocket(/ws:\/\/127\.0\.0\.1:.*\/ws/, (socket) => {
    socket.onMessage((raw) => {
      const request = JSON.parse(String(raw))
      const reply = (result: unknown) =>
        socket.send(
          JSON.stringify({
            protocol_version: 1,
            request_id: request.request_id,
            ok: true,
            result,
          }),
        )
      if (request.method === 'runtime.initialize') return reply({ protocolVersion: 1 })
      if (request.method === 'project.list')
        return reply({
          items: [
            {
              id: 'project',
              name: '工作区',
              path: '/workspace',
              writable: true,
              isGitRepository: true,
            },
          ],
        })
      if (request.method === 'session.list') return reply({ sessions: [session] })
      if (request.method === 'session.get') {
        reads += 1
        return reply(session)
      }
      if (request.method === 'workflow.list')
        return reply({
          items: [
            { id: 'single', name: '单角色', enabled: true },
            { id: 'review', name: '双角色', enabled: true },
            { id: 'disabled', name: '已停用', enabled: false },
          ],
        })
      if (request.method === 'session.update') {
        changes.push({ method: request.method, params: request.params })
        session.workflowId = request.params.workflowId
        return reply(session)
      }
      if (request.method === 'session.clear') {
        changes.push({ method: request.method, params: request.params })
        return reply({ cleared: true })
      }
      if (request.method === 'run.get')
        return reply({ agentRuns: [], reviews: [], artifacts: [], tasks: [] })
      return reply({ items: [], files: [] })
    })
  })
  return {
    changes,
    get reads() {
      return reads
    },
  }
}

function option(page: Page, label: string) {
  return page.locator('.n-dropdown-option-body').filter({ hasText: new RegExp(`^${label}$`) })
}

test('keeps workflow switching and context clearing reachable in the compact menu', async ({
  page,
}) => {
  const runtime = await sessionRuntime(page, 'completed')
  await page.goto('/#/sessions/options-session')
  const history = page.getByText('这是保留的历史答复。', { exact: true })
  await expect(history).toBeVisible()
  const before = runtime.reads
  const menu = page.getByRole('button', { name: '会话选项', exact: true })
  await menu.focus()
  await page.keyboard.press('Enter')
  await expect(option(page, '协作流程')).toBeVisible()
  await page.keyboard.press('ArrowDown')
  await page.keyboard.press('ArrowRight')
  await expect(option(page, '单角色（当前）')).toBeVisible()
  await page.keyboard.press('ArrowDown')
  await page.keyboard.press('Enter')
  await expect.poll(() => runtime.changes.length).toBe(1)
  expect(runtime.changes[0]).toEqual({
    method: 'session.update',
    params: { sessionId: 'options-session', workflowId: 'review' },
  })
  await expect.poll(() => runtime.reads).toBeGreaterThan(before)
  await menu.click()
  await option(page, '协作流程').hover()
  await expect(option(page, '双角色（当前）')).toBeVisible()
  await expect(option(page, '已停用')).toHaveCount(0)
  await option(page, '单角色').click()
  await expect.poll(() => runtime.changes.length).toBe(2)
  expect(runtime.changes[1]).toEqual({
    method: 'session.update',
    params: { sessionId: 'options-session', workflowId: 'single' },
  })
  await menu.click()
  await option(page, '清空上下文').click()
  await expect.poll(() => runtime.changes.length).toBe(3)
  expect(runtime.changes[2]).toEqual({
    method: 'session.clear',
    params: { sessionId: 'options-session' },
  })
  await expect(page.getByText('已清空模型上下文，历史记录仍保留', { exact: true })).toBeVisible()
  await expect(history).toBeVisible()
  await page.reload()
  await expect(history).toBeVisible()
})

for (const status of ['running', 'waiting'] as const) {
  test(`prevents workflow changes and context clearing while ${status} on mobile`, async ({
    page,
  }) => {
    const runtime = await sessionRuntime(page, status)
    await page.setViewportSize({ width: 390, height: 844 })
    await page.goto('/#/sessions/options-session')
    await page.getByRole('button', { name: '会话选项', exact: true }).click()
    for (const label of ['协作流程', '清空上下文']) {
      const item = option(page, label)
      await expect(item).toHaveClass(/n-dropdown-option-body--disabled/)
      await item.click({ force: true })
    }
    await page.keyboard.press('Escape')
    expect(runtime.changes).toEqual([])
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true)
    await page.getByRole('tab', { name: '调用轨迹', exact: true }).click()
    await expect(page.getByRole('button', { name: '停止运行', exact: true })).toBeVisible()
    if (status === 'waiting')
      await expect(page.getByRole('button', { name: /1 项等待输入/ })).toBeVisible()
  })
}
