import { expect, test, type Page, type WebSocketRoute } from '@playwright/test'

async function traceRuntime(page: Page, waiting = false) {
  const stamp = '2026-11-07T10:00:00Z'
  const runs = [
    { id: 'earlier', objective: '梳理项目结构', status: 'completed', createdAt: stamp },
    { id: 'latest', objective: '为工作区构建清晰的运行画布', status: waiting ? 'waiting' : 'running', createdAt: stamp },
  ]
  const calls: Record<string, unknown>[] = [
    { id: 'read', runId: 'latest', agentRunId: 'developer', toolName: 'read_file', taskKey: 'read', input: { path: 'src/app.vue' }, output: '<template>工作区</template>', status: 'completed', durationMs: 6, startedAt: stamp },
    { id: 'write', runId: 'latest', agentRunId: 'developer', toolName: 'write_file', taskKey: 'write', input: { path: 'src/canvas.vue', content: '<script>alert("escaped")</script>' }, output: '', status: 'running', durationMs: null, startedAt: stamp },
  ]
  const session = {
    id: 'trace-session', title: '界面设计与调用轨迹', projectId: 'project', status: waiting ? 'waiting' : 'running', runs,
    messages: runs.flatMap((run) => [
      { id: `user-${run.id}`, runId: run.id, role: 'user', content: run.objective, status: 'completed' },
      { id: `reply-${run.id}`, runId: run.id, role: 'assistant', content: run.id === 'latest' ? '已经读取项目入口，正在完善运行画布。\n\n- 保留工作区主题\n- 增加可追溯的调用记录' : '已完成项目结构分析。', status: 'completed' },
    ]),
    tasks: [],
    approvals: waiting ? [{ id: 'approval', runId: 'latest', action: '写入运行画布', kind: 'approval', status: 'pending', risk: 'write', expiresAt: new Date(Date.now() + 90_000).toISOString() }] : [],
  }
  let socket: WebSocketRoute
  let failLoad = false
  await page.routeWebSocket(/ws:\/\/127\.0\.0\.1:.*\/ws/, (connection) => {
    socket = connection
    socket.onMessage((raw) => {
      const request = JSON.parse(String(raw))
      const reply = (result: unknown) => socket.send(JSON.stringify({ protocol_version: 1, request_id: request.request_id, ok: true, result }))
      if (request.method === 'runtime.initialize') return reply({ protocolVersion: 1 })
      if (request.method === 'project.list') return reply({ items: [{ id: 'project', name: 'Aurora', path: '/workspace', writable: true, isGitRepository: true }] })
      if (request.method === 'session.list') return reply({ sessions: [session] })
      if (request.method === 'session.get') return reply(session)
      if (request.method === 'run.get') {
        if (failLoad) return socket.send(JSON.stringify({ protocol_version: 1, request_id: request.request_id, ok: false, error: { code: 'TEST', message: '本地验证读取失败' } }))
        return reply({ id: request.params.runId, toolCalls: request.params.runId === 'latest' ? calls : [], agentRuns: [{ id: 'developer', agentName: '前端开发者', stepKey: 'code', iteration: 0, modelName: '本地测试模型', status: 'running', output: '', error: '' }], reviews: [], artifacts: [] })
      }
      return reply({ items: [], files: [] })
    })
  })
  return {
    fail() { failLoad = true },
    recover() { failLoad = false },
    complete() {
      const call = calls[1]!
      Object.assign(call, { status: 'failed', durationMs: 123, error: '写入失败：目标为只读文件', endedAt: '2026-11-07T10:00:01Z' })
      socket.send(JSON.stringify({ protocol_version: 1, event_id: crypto.randomUUID(), type: 'tool.failed', run_id: 'latest', session_id: session.id, occurred_at: new Date().toISOString(), payload: { ...call, tool_call_id: call.id } }))
    },
  }
}

test('shows only the live timeline and preserves per-run navigation through conversation', async ({ page }) => {
  const runtime = await traceRuntime(page)
  const errors: string[] = []
  page.on('pageerror', error => errors.push(error.message))
  await page.goto('/#/sessions/trace-session')
  expect(await page.locator('.user-message, .assistant-message p, .agent-monogram, .run-kicker').evaluateAll(elements => elements.every(element => getComputedStyle(element).fontFamily.includes('PingFang SC')))).toBe(true)
  await page.getByRole('button', { name: '查看第 2 轮调用轨迹' }).click()
  await expect(page.getByRole('tab', { name: '调用轨迹' })).toHaveAttribute('aria-selected', 'true')
  await expect(page.getByRole('textbox', { name: '任务内容' })).not.toBeVisible()
  await expect(page.getByRole('button', { name: '停止运行', exact: true })).toBeVisible()
  const timeline = page.getByRole('region', { name: '调用时间线', exact: true })
  await expect(timeline.locator('.call-item')).toHaveCount(2)
  await expect(timeline.locator('aside, .trace-metrics, .stage-strip, .call-filters, .trace-search, .run-review')).toHaveCount(0)
  const write = page.locator('[data-call-id="write"]')
  await write.getByRole('button').click()
  await expect(write.locator('.call-detail pre')).toContainText('escaped')
  expect(await write.locator('.tool-name, .call-timing, .call-detail pre').evaluateAll(elements => elements.every(element => getComputedStyle(element).fontFamily.includes('PingFang SC')))).toBe(true)
  expect(await page.locator('.call-detail script').count()).toBe(0)
  await expect(write.getByText('执行中 · 完成后显示耗时')).toBeVisible()
  runtime.complete()
  await expect(write.getByText('123 ms')).toBeVisible()
  await expect(write.getByText('写入失败：目标为只读文件', { exact: true })).toBeVisible()
  await page.getByRole('tab', { name: '对话', exact: true }).click()
  await page.getByRole('button', { name: '查看第 1 轮调用轨迹' }).click()
  await expect(page.getByText('暂无工具调用记录', { exact: true })).toBeVisible()
  await page.getByRole('tab', { name: '对话', exact: true }).click()
  await page.getByRole('button', { name: '查看第 2 轮调用轨迹' }).click()
  await expect(page.locator('[data-call-id="write"]')).toBeVisible()
  runtime.fail()
  await page.getByRole('tab', { name: '对话', exact: true }).click()
  await page.getByRole('tab', { name: '调用轨迹' }).click()
  await expect(page.getByRole('alert')).toContainText('本地验证读取失败')
  await expect(page.locator('[data-call-id="read"]')).toBeVisible()
  runtime.recover()
  await timeline.getByRole('button', { name: '重试', exact: true }).click()
  await expect(page.locator('.trace-error')).not.toBeVisible()
  await page.screenshot({ path: '/tmp/aurora-call-trace-desktop.png', animations: 'disabled' })
  await page.getByRole('tab', { name: '对话', exact: true }).click()
  await expect(page.getByRole('textbox', { name: '任务内容' })).toBeVisible()
  expect(errors).toEqual([])
})

test('keeps the mobile timeline uncluttered and pending approval reachable', async ({ page }) => {
  await traceRuntime(page, true)
  await page.setViewportSize({ width: 390, height: 844 })
  await page.goto('/#/sessions/trace-session')
  await page.getByRole('tab', { name: '对话', exact: true }).focus()
  await page.keyboard.press('ArrowRight')
  await expect(page.getByRole('tab', { name: '调用轨迹' })).toBeFocused()
  await expect(page.locator('[data-call-id="read"]')).toBeVisible()
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true)
  await expect(page.locator('.trace-panel aside, .trace-panel h2, .trace-panel input')).toHaveCount(0)
  await page.screenshot({ path: '/tmp/aurora-call-trace-mobile.png', animations: 'disabled' })
  await page.getByRole('button', { name: /1 项等待输入/ }).click()
  await expect(page.getByRole('button', { name: '允许', exact: true })).toBeInViewport()
  await page.getByRole('tab', { name: '对话', exact: true }).focus()
  await page.keyboard.press('End')
  await page.keyboard.press('Home')
  await expect(page.getByRole('tab', { name: '对话', exact: true })).toBeFocused()
  await expect(page.getByRole('textbox', { name: '任务内容' })).toBeVisible()
})
