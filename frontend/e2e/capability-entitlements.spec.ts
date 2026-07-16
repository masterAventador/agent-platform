import { expect, test } from '@playwright/test'

import { registerAndLogin } from './helpers/auth'

/**
 * C17 能力授权真实链路验收：不 mock capabilities/registry，
 * 由真实后端按「部署安装 ∩ 租户 Entitlement ∩ 用户 RBAC」三层裁剪驱动前端。
 */

test('C17 未授权租户看不到能力入口且直达路由被拒', async ({ page }) => {
  await registerAndLogin(page)

  await expect(page.getByRole('link', { name: '工作台' })).toBeVisible()
  await expect(page.getByRole('link', { name: '设备与平台账号' })).toHaveCount(0)

  await page.goto('/video/account')
  await expect(page.getByText('当前工作区未获此能力授权')).toBeVisible()
  await expect(page.getByRole('heading', { name: '设备与平台账号中心' })).toHaveCount(0)
})

test('C17 授权后菜单可见可用，撤销后入口消失且直达被拒', async ({ page }) => {
  await page.addInitScript(() => {
    Reflect.set(globalThis, '__AGENT_PLATFORM_TEST_ADAPTER__', 'social-operations')
  })
  await registerAndLogin(page)

  await expect(page.getByRole('link', { name: '设备与平台账号' })).toHaveCount(0)

  const grant = await page.request.put(
    '/api/v1/capabilities/entitlements/social-operations',
    { data: { source: 'manual' } },
  )
  expect(grant.ok()).toBeTruthy()

  await page.reload()
  const entryLink = page.getByRole('link', { name: '设备与平台账号' })
  await expect(entryLink).toBeVisible()
  await entryLink.click()
  await expect(page).toHaveURL(/\/video\/account$/)
  await expect(page.getByRole('heading', { name: '设备与平台账号中心' })).toBeVisible()

  const deviceId = crypto.randomUUID()
  await page.getByLabel('设备 ID').fill(deviceId)
  await page.getByLabel('设备名称').fill('C17 验收设备')
  await page.getByRole('button', { name: '注册本机设备' }).click()
  await expect(page.getByText('本机设备已注册。')).toBeVisible()

  const revoke = await page.request.delete(
    '/api/v1/capabilities/entitlements/social-operations',
  )
  expect(revoke.ok()).toBeTruthy()

  await page.reload()
  await expect(page.getByRole('link', { name: '工作台' })).toBeVisible()
  await expect(page.getByRole('link', { name: '设备与平台账号' })).toHaveCount(0)
  await page.goto('/video/account')
  await expect(page.getByText('当前工作区未获此能力授权')).toBeVisible()

  const rejected = await page.request.post(
    '/api/v1/social-operations/devices/register',
    {
      data: {
        device_id: crypto.randomUUID(),
        display_name: '撤销后不应注册',
        platform: 'macos',
        app_version: '0.1.0',
        executor_version: '1.0.0',
      },
    },
  )
  expect(rejected.status()).toBe(403)
})
