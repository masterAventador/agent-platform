import { expect, test } from '@playwright/test'
import type { Page } from '@playwright/test'

import { registerAndLogin } from './helpers/auth'

const deviceId = '00000000-0000-4000-8000-000000000301'
const accountId = '00000000-0000-4000-8000-000000000501'
const socialPermissions = ['social.read', 'social.manage', 'social.execute']

async function mockCapabilityRegistry(
  page: Page,
  tenantEntitled: boolean,
  frontendEntries = ['social.routes.v1'],
) {
  await page.route('**/api/v1/capabilities/registry', (route) => route.fulfill({
    contentType: 'application/json',
    json: {
      schema_version: '1.0',
      capabilities: [{
        capability_id: 'social-operations',
        deployment_installed: true,
        tenant_entitled: tenantEntitled,
        frontend_entries: frontendEntries,
        permissions: socialPermissions,
      }],
    },
  }))
}

function trackSocialModuleRequests(page: Page): string[] {
  const requests: string[] = []
  page.on('request', (request) => {
    if (request.url().includes('/src/features/social-operations/module.tsx')) {
      requests.push(request.url())
    }
  })
  return requests
}

async function grantSocialPermissionsOnLogin(page: Page) {
  await page.route('**/api/v1/auth/login', async (route) => {
    const response = await route.fetch()
    const user = await response.json()
    await route.fulfill({
      response,
      json: {
        ...user,
        workspaces: user.workspaces.map((workspace: { permissions: string[] }) => ({
          ...workspace,
          permissions: [...workspace.permissions, ...socialPermissions],
        })),
      },
    })
  })
}

test('B02 生产入口通过 Tauri 适配器执行受控账号流程', async ({ page }) => {
  const moduleRequests = trackSocialModuleRequests(page)
  await mockCapabilityRegistry(page, true)
  await grantSocialPermissionsOnLogin(page)
  await page.addInitScript(() => {
    Reflect.set(globalThis, '__AGENT_PLATFORM_TEST_ADAPTER__', 'social-operations')
  })

  await page.route('**/api/v1/social-operations/devices**', async (route) => {
    if (route.request().method() === 'POST') {
      const tenantId = route.request().headers()['x-tenant-id']
      await route.fulfill({
        contentType: 'application/json',
        json: {
          device_id: deviceId,
          tenant_id: tenantId,
          owner_user_id: '00000000-0000-4000-8000-000000000201',
          display_name: 'E2E Mac',
          platform: 'macos',
          app_version: '0.1.0',
          executor_version: '1.0.0',
          registered_at: '2026-07-15T02:00:00Z',
          last_seen_at: '2026-07-15T02:00:00Z',
          status: 'online',
          heartbeat_sequence: 0,
        },
      })
      return
    }
    await route.fulfill({ contentType: 'application/json', json: [] })
  })

  await registerAndLogin(page)
  await page.getByRole('link', { name: '设备与平台账号' }).click()
  await expect(page).toHaveURL(/\/video\/account$/)
  await expect(page.getByRole('heading', { name: '设备与平台账号中心' })).toBeVisible()
  expect(moduleRequests).toHaveLength(1)

  await page.getByLabel('设备 ID').fill(deviceId)
  await page.getByLabel('设备名称').fill('E2E Mac')
  await page.getByRole('button', { name: '注册本机设备' }).click()
  await expect(page.getByText('本机设备已注册。')).toBeVisible()

  await page.getByLabel('平台账号 ID').fill(accountId)
  await page.getByRole('button', { name: '准备账号环境' }).click()
  await expect(page.getByText('账号私有目录已准备。')).toBeVisible()
  await expect(page.getByText('已注销')).toBeVisible()
  await page.getByRole('button', { name: '开始扫码' }).click()
  await expect(page.getByText('等待扫码')).toBeVisible()
  await page.getByRole('button', { name: '确认已完成扫码' }).click()
  await expect(page.getByText('等待确认')).toBeVisible()
  await page.getByRole('button', { name: '确认已登录' }).click()
  await expect(page.getByText('健康', { exact: true })).toBeVisible()
  await page.getByRole('button', { name: '启动本地执行器' }).click()
  await expect(page.getByText('执行器运行中')).toBeVisible()
  await page.getByRole('button', { name: '执行无副作用健康检查' }).click()
  await expect(page.getByText('无副作用健康检查已接受。')).toBeVisible()
  await page.getByRole('button', { name: '生成安全诊断' }).click()
  await expect(page.getByText('cookie=[REDACTED]')).toBeVisible()
  await page.getByRole('button', { name: '紧急停止' }).click()
  await expect(page.getByText('紧急停止已生效。')).toBeVisible()

  const commands = await page.evaluate(() => Reflect.get(globalThis, '__socialCommands'))
  expect(commands).toEqual(expect.arrayContaining([
    expect.objectContaining({ command: 'social_account_prepare' }),
    expect.objectContaining({
      command: 'social_account_login_signal',
      args: expect.objectContaining({ signal: 'begin_qr' }),
    }),
    expect.objectContaining({
      command: 'social_account_login_signal',
      args: expect.objectContaining({ signal: 'qr_scanned' }),
    }),
    expect.objectContaining({
      command: 'social_account_login_signal',
      args: expect.objectContaining({ signal: 'authenticated' }),
    }),
    expect.objectContaining({ command: 'social_account_start' }),
    expect.objectContaining({ command: 'social_account_invoke' }),
    expect.objectContaining({ command: 'social_executor_take_safe_diagnostics' }),
    expect.objectContaining({ command: 'social_account_emergency_stop' }),
  ]))
  expect(commands).not.toEqual(expect.arrayContaining([
    expect.objectContaining({ command: 'local_executor_start' }),
    expect.objectContaining({ command: 'local_executor_invoke' }),
  ]))
})

test('B02 租户未授权时隐藏入口并拒绝直达账号路由', async ({ page }) => {
  const moduleRequests = trackSocialModuleRequests(page)
  await mockCapabilityRegistry(page, false)
  await registerAndLogin(page)

  await expect(page.getByRole('link', { name: '设备与平台账号' })).toHaveCount(0)
  await page.goto('/tiktok/account')
  await expect(page.getByText('当前工作区未获此能力授权')).toBeVisible()
  await expect(page.getByRole('heading', { name: '设备与平台账号中心' })).toHaveCount(0)
  expect(moduleRequests).toHaveLength(0)
})

test('B02 frontend_entries 恶意漂移时拒绝且不下载业务模块', async ({ page }) => {
  const moduleRequests = trackSocialModuleRequests(page)
  await mockCapabilityRegistry(page, true, ['social.routes.v1', 'social.evil.v1'])
  await grantSocialPermissionsOnLogin(page)
  await registerAndLogin(page)

  await page.goto('/video/account')
  await expect(page.getByText('能力清单与客户端模块不兼容')).toBeVisible()
  await expect(page.getByRole('link', { name: '设备与平台账号' })).toHaveCount(0)
  expect(moduleRequests).toHaveLength(0)
})
