import { expect, test } from '@playwright/test'

import { registerAndLogin } from './helpers/auth'


test('owner 可以从运维入口查看审计与观测页并定位 correlation_id', async ({ page }) => {
  await registerAndLogin(page)

  await page.getByRole('link', { name: '审计与观测' }).click()

  await expect(page).toHaveURL(/\/operations\/audit-observability$/)
  await expect(page.getByRole('heading', { name: '审计与观测' })).toBeVisible()
  await expect(page.getByRole('link', { name: '打开 Jaeger 本机链路追踪' })).toHaveAttribute(
    'href',
    'http://127.0.0.1:16686/',
  )
  await expect(page.getByText('auth.login_succeeded')).toBeVisible()
  await expect(page.getByText('JSONL 导出接口')).toBeVisible()
  await expect(page.locator('body')).not.toContainText('correct horse battery staple')
})
