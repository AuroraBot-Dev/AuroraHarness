import { expect, test } from '@playwright/test'

test('keeps workspace navigation focused and accessible on desktop and mobile', async ({
  page,
}) => {
  const projects = [
    { id: 'web', name: '前端项目', path: '/workspace/web', isGitRepository: true, writable: true },
    { id: 'api', name: 'API 服务', path: '/workspace/api', isGitRepository: true, writable: true },
    { id: 'docs', name: '文档', path: '/workspace/docs', isGitRepository: true, writable: true },
  ]
  const sessions = [
    {
      id: 'web-session',
      title: '设计首页',
      projectId: 'web',
      status: 'idle',
      messages: [],
      runs: [],
      tasks: [],
    },
    {
      id: 'api-session',
      title: '审查 API',
      projectId: 'api',
      status: 'idle',
      messages: [],
      runs: [],
      tasks: [],
    },
  ]
  const createdPaths: string[] = []
  await page.routeWebSocket(/ws:\/\/127\.0\.0\.1:.*\/ws/, (socket) => {
    socket.onMessage((raw) => {
      const request = JSON.parse(String(raw))
      let result: unknown = { items: [] }
      if (request.method === 'runtime.initialize') result = { protocolVersion: 1, capabilities: [] }
      if (request.method === 'project.list') result = { items: projects }
      if (request.method === 'session.list') result = { sessions }
      if (request.method === 'git.status')
        result = {
          view: 'workspace',
          branch: 'main',
          files: [],
          canRollback: false,
          rollbackReason: '',
        }
      if (request.method === 'workspace.validate')
        result = projects.find((project) => project.path === request.params.path)
      if (request.method === 'session.get')
        result = sessions.find((session) => session.id === request.params.sessionId)
      if (request.method === 'session.create') {
        createdPaths.push(request.params.workspacePath)
        const project = projects.find((item) => item.path === request.params.workspacePath)!
        const session = {
          id: 'new-session',
          title: request.params.title,
          projectId: project.id,
          status: 'idle',
          messages: [],
          runs: [],
          tasks: [],
        }
        sessions.push(session)
        result = { sessionId: session.id, projectId: project.id, workflowId: 'single' }
      }
      socket.send(
        JSON.stringify({ protocol_version: 1, request_id: request.request_id, ok: true, result }),
      )
    })
  })
  const errors: string[] = []
  page.on('pageerror', (error) => errors.push(error.message))
  await page.setViewportSize({ width: 1440, height: 900 })
  await page.emulateMedia({ reducedMotion: 'reduce' })
  await page.goto('/#/')
  const navigator = page.locator('.workspace-navigator')
  await expect(navigator.getByRole('button', { name: /设计首页/ }).first()).toBeVisible()
  await expect(navigator.getByRole('button', { name: /审查 API/ })).toHaveCount(0)
  await page.getByRole('button', { name: '切换到 API 服务', exact: true }).click()
  await expect(page.getByRole('heading', { name: 'API 服务', exact: true })).toBeVisible()
  await expect(navigator.getByRole('button', { name: /审查 API/ }).first()).toBeVisible()
  await expect(navigator.getByRole('button', { name: /设计首页/ })).toHaveCount(0)
  await page.getByRole('button', { name: '新对话', exact: true }).click()
  await expect(page).toHaveURL(/sessions\/new-session/)
  expect(createdPaths).toEqual(['/workspace/api'])
  await page.getByRole('link', { name: '工作台', exact: true }).click()
  await expect(page).toHaveURL(/#\/$/)
  await page.reload()
  await expect(
    navigator.getByRole('button', { name: '切换到 API 服务', exact: true }),
  ).toHaveAttribute('aria-pressed', 'true')
  await expect(page.getByRole('heading', { name: /今天，\s*想做点什么？/ })).toBeVisible()
  await page.screenshot({ path: '/tmp/aurora-workspace-sidebar.png', animations: 'disabled' })
  await page.getByRole('button', { name: '工作区', exact: true }).click()
  await expect(navigator).toHaveCount(0)
  await expect(page.locator('.rail-runtime .status-label')).toBeHidden()
  for (const label of ['工作台', 'Agent', '提示词库', '设置'])
    await expect(
      page.locator('.navigation-rail').getByRole('link', { name: label, exact: true }),
    ).toBeVisible()
  const centers = await page.locator('.rail-navigation .rail-button').evaluateAll((elements) =>
    elements.map((element) => {
      const bounds = element.getBoundingClientRect()
      return bounds.x + bounds.width / 2
    }),
  )
  expect(centers.every((center) => Math.abs(center - 42) < 1)).toBe(true)
  await page.reload()
  await expect(navigator).toHaveCount(0)
  await page.getByRole('button', { name: '工作区', exact: true }).click()
  await expect(navigator).toBeVisible()
  await page.screenshot({ path: '/tmp/aurora-navigation-expanded.png', animations: 'disabled' })
  await page.setViewportSize({ width: 390, height: 844 })
  await page.getByRole('button', { name: '工作区', exact: true }).click()
  await expect(page.getByRole('button', { name: '切换到 文档', exact: true })).toBeVisible()
  await expect(page.locator('.workspace-overlay')).toBeInViewport({ ratio: 1 })
  await page.screenshot({ path: '/tmp/aurora-workspace-mobile.png', animations: 'disabled' })
  await page
    .locator('.workspace-overlay')
    .getByRole('button', { name: /审查 API/ })
    .first()
    .click()
  await expect(page).toHaveURL(/sessions\/api-session/)
  await expect(page.locator('.n-drawer')).toHaveCount(0)
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(
    true,
  )
  expect(errors).toEqual([])
})
