import { expect, test } from '@playwright/test'

test('manages Agent configurations and refreshes live status', async ({ page }) => {
  const models = [
    { id: 'text', name: '测试文本', modelName: 'text-model', modelType: 'text', inputTypes: ['text'] },
    { id: 'vision', name: '视觉模型', modelName: 'vision-model', modelType: 'multimodal', inputTypes: ['text', 'image'] },
    { id: 'vector', name: '向量模型', modelName: 'embedding-model', modelType: 'embedding', inputTypes: ['text'] },
  ]
  const agents = models.map((model) => ({ id: model.id, name: model.name, modelConfigId: model.id, description: '按职责完成子任务', instructions: '完成分配的任务', allowedTools: [] as string[], enabled: true, callable: model.id !== 'vector' }))
  const tests: Record<string, { status: string; testedAt: string }> = {}
  let running = true
  let polls = 0
  await page.routeWebSocket(/ws:\/\/127\.0\.0\.1:.*\/ws/, (ws) => {
    ws.onMessage((raw) => {
      const request = JSON.parse(String(raw))
      const data = request.params?.data
      let result: unknown = {}
      if (request.method === 'runtime.initialize') result = { protocolVersion: 1, capabilities: [] }
      if (request.method === 'project.list') result = { items: [] }
      if (request.method === 'session.list') result = { sessions: [] }
      if (request.method === 'agent.list') result = { items: agents }
      if (request.method === 'model.list') result = { items: models }
      if (request.method === 'agent.create') {
        const created = { ...data, id: 'new', modelConfigId: data.model_config_id, allowedTools: data.allowed_tools }
        agents.push(created)
        result = created
      }
      if (request.method === 'agent.update') {
        const agent = agents.find((item) => item.id === request.params.id)!
        Object.assign(agent, data, data.model_config_id ? { modelConfigId: data.model_config_id, allowedTools: data.allowed_tools } : {})
        result = agent
      }
      if (request.method === 'agent.test') result = tests[request.params.id] = { status: 'passed', testedAt: new Date().toISOString() }
      if (request.method === 'agent.status') {
        polls++
        result = { items: agents.map((a) => ({ id: a.id, modelName: models.find((m) => m.id === a.modelConfigId)!.modelName, modelType: models.find((m) => m.id === a.modelConfigId)!.modelType, issues: a.id === 'vector' ? ['向量模型暂不支持调用'] : [], test: tests[a.id] ?? {}, runningCount: running && a.id === 'vision' ? 1 : 0, waitingCount: 0, active: running && a.id === 'vision' ? [{ id: 'ar1', session_id: 's1', status: 'running', task: '检查登录页截图' }] : [], latest: a.id === 'vision' ? { id: 'ar1', run_id: 'r1', session_id: 's1', status: running ? 'running' : 'completed', output: running ? '' : '截图检查完成', error: '', updated_at: '' } : null })) }
      }
      ws.send(JSON.stringify({ protocol_version: 1, request_id: request.request_id, ok: true, result }))
    })
  })
  const errors: string[] = []
  page.on('pageerror', (error) => errors.push(error.message))
  await page.goto('/#/agents')
  await expect(page.getByRole('heading', { name: 'Agent', exact: true })).toBeVisible()
  await expect(page.getByText('运行中：检查登录页截图 →')).toBeVisible()
  await expect(page.getByText('向量模型暂不支持调用', { exact: true })).toBeVisible()
  await page.getByRole('button', { name: '新建 Agent', exact: true }).click()
  const drawer = page.locator('.n-drawer')
  await drawer.getByRole('textbox').first().fill('文案专家')
  await drawer.getByPlaceholder('例如：理解页面截图，定位布局与视觉问题').fill('撰写简洁的产品文案')
  await drawer.locator('.n-base-selection').click()
  await page.getByText('测试文本 · 文本', { exact: true }).click()
  await drawer.getByPlaceholder('描述职责、执行步骤和输出要求').fill('根据任务撰写中文文案')
  await drawer.getByText('允许主 Agent 调用', { exact: true }).click()
  await drawer.getByRole('button', { name: '保存 Agent' }).click()
  const card = page.locator('.agent-card').filter({ hasText: '文案专家' })
  await expect(card).toBeVisible()
  expect(agents.find((a) => a.id === 'new')?.callable).toBe(true)
  await card.getByRole('button', { name: '测试连接' }).click()
  await expect(card.getByText(/连接测试通过/)).toBeVisible()
  await card.getByRole('button', { name: '编辑', exact: true }).click()
  await drawer.getByPlaceholder('描述职责、执行步骤和输出要求').fill('使用新的写作要求')
  await drawer.getByRole('button', { name: '保存 Agent' }).click()
  await expect(drawer).toHaveCount(0)
  expect(agents.find((a) => a.id === 'new')?.instructions).toBe('使用新的写作要求')
  await card.getByRole('button', { name: '停用', exact: true }).click()
  await expect(card.getByText('已停用', { exact: true })).toBeVisible()
  const before = polls
  running = false
  await expect.poll(() => polls, { timeout: 7000 }).toBeGreaterThan(before)
  await expect(page.getByText('截图检查完成', { exact: true })).toBeVisible()
  await expect(page.getByRole('link', { name: /最近执行：已完成/ })).toHaveAttribute('href', /sessions\/s1#agent-run-ar1/)
  await page.screenshot({ path: '/tmp/aurora-agents-desktop.png', fullPage: true })
  await page.setViewportSize({ width: 620, height: 800 })
  await expect(card).toBeVisible()
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true)
  await page.screenshot({ path: '/tmp/aurora-agents-mobile.png', fullPage: true })
  expect(errors).toEqual([])
})
