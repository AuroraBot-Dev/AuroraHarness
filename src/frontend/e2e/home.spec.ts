import { expect, test } from '@playwright/test'

test('renders the disconnected workspace entry screen', async ({ page }) => {
  await page.goto('/')
  await expect(page).toHaveTitle(/Aurora Agent/)
  await expect(page.getByRole('heading', { name: '今天要完成什么？' })).toBeVisible()
  await expect(page.getByRole('button', { name: /添加工作区/ })).toBeVisible()
})
