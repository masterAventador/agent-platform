import { expect, test } from '@playwright/test'

test('打开平台并显示后端服务正常', async ({ page }) => {
  await page.goto('/')

  await expect(page.getByRole('heading', { name: 'AI 数字员工平台' })).toBeVisible()
  await expect(page.getByRole('link', { name: '数字员工' })).toBeVisible()
  await expect(page.getByText('后端服务正常')).toBeVisible()
})
