import { expect, test } from '@playwright/test'

import { registerAndLogin } from './helpers/auth'


test('容器部署通过同源 API 完成登录并支持 SPA 深链接刷新', async ({ page }) => {
  const healthResponse = await page.request.get('/api/v1/health/live')
  expect(healthResponse.ok()).toBe(true)
  await expect(healthResponse.json()).resolves.toEqual({ status: 'ok' })

  const email = await registerAndLogin(page)
  await expect(page.getByText(email)).toBeVisible()

  await page.goto('/employees')
  await expect(page.getByRole('heading', { name: '数字员工', exact: true })).toBeVisible()

  await page.reload()
  await expect(page).toHaveURL(/\/employees$/)
  await expect(page.getByRole('heading', { name: '数字员工', exact: true })).toBeVisible()
  await expect(page.getByText(email)).toBeVisible()
})
