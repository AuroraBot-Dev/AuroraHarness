import { expect, test } from '@playwright/test'

test.beforeEach(async ({ page }) => {
  await page.routeWebSocket(/ws:\/\/127\.0\.0\.1:.*\/ws/, (socket) => {
    socket.onMessage((raw) => {
      const request = JSON.parse(String(raw))
      const result =
        request.method === 'runtime.initialize'
          ? { protocolVersion: 1, capabilities: [] }
          : request.method === 'session.list'
            ? { sessions: [] }
            : { items: [] }
      socket.send(
        JSON.stringify({ protocol_version: 1, request_id: request.request_id, ok: true, result }),
      )
    })
  })
})

test('saves a prompt, fills its variables, and restores it after reloading', async ({ page }) => {
  const errors: string[] = []
  page.on('pageerror', (error) => errors.push(error.message))
  await page.goto('/#/prompts')
  await page.getByRole('button', { name: '新建提示词', exact: true }).click()
  await page.getByRole('textbox', { name: '提示词名称', exact: true }).fill('我的审查习惯')
  await page.getByRole('textbox', { name: '提示词简介', exact: true }).fill('关注边界与可读性')
  await page
    .getByRole('textbox', { name: '提示词正文', exact: true })
    .fill('请审查 {{范围}}，重点关注 {{重点}}。')
  await page.getByRole('button', { name: '保存提示词', exact: true }).click()
  const card = page.locator('.prompt-card').filter({ hasText: '我的审查习惯' })
  await expect(card).toBeVisible()
  await page.reload()
  await card.getByRole('button', { name: '使用提示词' }).click()
  await expect(page.getByRole('button', { name: '填入工作台' })).toBeDisabled()
  await page.getByRole('textbox', { name: '范围', exact: true }).fill('当前修改')
  await page.getByRole('textbox', { name: '重点', exact: true }).fill('跨平台行为')
  await expect(page.locator('.prompt-preview')).toHaveText('请审查 当前修改，重点关注 跨平台行为。')
  await page.getByRole('button', { name: '填入工作台' }).click()
  await expect(page.getByRole('textbox', { name: '任务内容' })).toHaveValue(
    '请审查 当前修改，重点关注 跨平台行为。',
  )
  expect(errors).toEqual([])
})

test('supports hex colors, persistence and responsive navigation', async ({ page }) => {
  await page.goto('/#/settings')
  const color = page.getByRole('textbox', { name: '强调色十六进制编码' })
  await color.fill('#12zz56')
  await expect(page.getByRole('button', { name: '应用', exact: true })).toBeDisabled()
  await color.fill('#7955C7')
  await page.getByRole('button', { name: '应用', exact: true }).click()
  await page.reload()
  await expect(color).toHaveValue('#7955C7')
  for (const width of [390, 768, 1280]) {
    await page.setViewportSize({ width, height: 844 })
    await page.getByRole('link', { name: '提示词库', exact: true }).click()
    await expect(page.getByRole('heading', { name: '提示词库', exact: true })).toBeVisible()
    expect(
      await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth),
    ).toBe(true)
    expect(
      await page
        .locator('.library-page')
        .evaluate((element) => element.scrollWidth <= element.clientWidth),
    ).toBe(true)
    await page.getByRole('button', { name: '新建提示词', exact: true }).click()
    await expect(page.getByRole('textbox', { name: '提示词正文', exact: true })).toBeVisible()
    const bounds = await page.locator('.n-drawer').boundingBox()
    expect(bounds!.width).toBeLessThanOrEqual(width)
    await page.locator('.n-drawer-header__close').click()
  }
})

test('uses an edge-to-edge canvas and remembers the glass material preference', async ({
  page,
}) => {
  await page.setViewportSize({ width: 1440, height: 900 })
  await page.emulateMedia({ reducedMotion: 'reduce', colorScheme: 'light' })
  await page.goto('/#/')
  const shell = page.locator('.app-shell')
  const content = page.locator('.app-content')
  const composer = page.locator('.composer-shell')
  await expect(page.getByRole('heading', { name: /今天，\s*想做点什么？/ })).toBeVisible()
  const shellBounds = await shell.boundingBox()
  const contentBounds = await content.boundingBox()
  expect(shellBounds).toEqual({ x: 0, y: 0, width: 1440, height: 900 })
  expect(contentBounds!.y).toBe(0)
  expect(contentBounds!.x + contentBounds!.width).toBe(1440)
  expect(contentBounds!.height).toBe(900)
  expect(await content.evaluate((element) => getComputedStyle(element).borderRadius)).toBe('0px')
  expect(
    await page
      .locator('.home-compose')
      .evaluate((element) => getComputedStyle(element).borderTopWidth),
  ).toBe('0px')
  expect(await composer.evaluate((element) => getComputedStyle(element).backdropFilter)).toContain(
    'blur(32px)',
  )
  await page.screenshot({ path: '/tmp/aurora-canvas-light.png' })

  await page.getByRole('link', { name: '设置', exact: true }).click()
  await page.getByText('纯色表面', { exact: true }).click()
  await page.reload()
  await expect(page.getByRole('radio', { name: '纯色表面', exact: true })).toBeChecked()
  await page.getByRole('link', { name: '工作台', exact: true }).click()
  expect(await composer.evaluate((element) => getComputedStyle(element).backdropFilter)).toContain(
    'blur(0px)',
  )

  await page.getByRole('link', { name: '设置', exact: true }).click()
  await page.getByText('磨砂玻璃', { exact: true }).click()
  await page.getByText('深色', { exact: true }).click()
  await page.getByRole('link', { name: '工作台', exact: true }).click()
  await page.screenshot({ path: '/tmp/aurora-canvas-dark.png' })
  for (const width of [320, 390, 768]) {
    await page.setViewportSize({ width, height: 844 })
    await expect(page.getByRole('textbox', { name: '任务内容' })).toBeVisible()
    expect(
      await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth),
    ).toBe(true)
    expect(
      await page
        .locator('.home-page')
        .evaluate((element) => element.scrollWidth <= element.clientWidth),
    ).toBe(true)
    if (width === 390) await page.screenshot({ path: '/tmp/aurora-canvas-mobile.png' })
  }
})
