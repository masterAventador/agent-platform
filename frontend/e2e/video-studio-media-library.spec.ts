import { expect, test, type Page } from '@playwright/test'

const reusableEmail = 'e2e-video-studio@example.com'
const reusablePassword = 'agent-platform-video-e2e'
const videoPermissions = ['video.read', 'video.manage', 'video.execute']

async function enableVideoStudioCapability(page: Page) {
  await page.route('**/api/v1/capabilities/registry', (route) => route.fulfill({
    contentType: 'application/json',
    json: {
      schema_version: '1.0',
      capabilities: [{
        capability_id: 'video-studio',
        deployment_installed: true,
        tenant_entitled: true,
        frontend_entries: ['video.routes.v1'],
        permissions: videoPermissions,
      }],
    },
  }))
  await page.route('**/api/v1/auth/login', async (route) => {
    const response = await route.fetch()
    const user = await response.json()
    await route.fulfill({
      response,
      json: {
        ...user,
        workspaces: user.workspaces.map((workspace: { permissions: string[] }) => ({
          ...workspace,
          permissions: [...workspace.permissions, ...videoPermissions],
        })),
      },
    })
  })
}

async function loginWithReusableAccount(page: Page) {
  const registration = await page.request.post('/api/v1/auth/register', {
    data: { email: reusableEmail, password: reusablePassword },
  })
  expect([201, 409]).toContain(registration.status())

  await page.goto('/login')
  await page.getByLabel('邮箱').fill(reusableEmail)
  await page.getByLabel('密码', { exact: true }).fill(reusablePassword)
  await page.getByRole('button', { name: /登\s*录/ }).click()
  await page.waitForURL(/\/$/)
}

test('B04 素材文件直传、确认、预览、下载状态与删除形成真实 API 闭环', async ({ page }) => {
  await enableVideoStudioCapability(page)
  const cosRequests: string[] = []
  await page.route(/^https?:\/\/[^/]+\.cos\./, async (route) => {
    cosRequests.push(route.request().url())
    const requestOrigin = route.request().headers().origin
    await route.fulfill({
      status: 200,
      headers: {
        'access-control-allow-origin': requestOrigin ?? '*',
        'access-control-expose-headers': 'etag',
        etag: '"b04-e2e-etag"',
      },
      body: '',
    })
  })

  await loginWithReusableAccount(page)
  await page.getByRole('link', { name: '素材库' }).click()
  await expect(page).toHaveURL(/\/video\/materials$/)
  await expect(page.getByRole('heading', { name: '素材库' })).toBeVisible()

  await page.getByLabel('文件夹名称').fill('B04 E2E 素材')
  await page.getByRole('button', { name: '创建文件夹' }).click()
  await expect(page.getByText('素材文件夹已创建。')).toBeVisible()

  await page.getByLabel('素材文件', { exact: true }).setInputFiles({
    name: 'b04-preview.png',
    mimeType: 'image/png',
    buffer: Buffer.from('B04 isolated browser upload'),
  })
  await page.getByLabel('素材类型').selectOption('image')
  await page.getByLabel('标签').fill('封面,E2E')
  await page.getByRole('button', { name: '上传到素材库' }).click()
  await expect(page.getByText('素材已直传，等待服务端核验。')).toBeVisible()
  expect(cosRequests).toHaveLength(1)
  await expect(page.getByText('上传进度：100%')).toBeVisible()

  await page.getByRole('button', { name: '确认上传完成' }).click()
  await expect(page.getByText('素材已确认上传完成。')).toBeVisible()
  await expect(page.getByRole('cell', { name: 'b04-preview.png', exact: true })).toBeVisible()
  await expect(page.getByRole('cell', { name: 'available' })).toBeVisible()

  await page.getByRole('button', { name: '预览 b04-preview.png' }).click()
  await expect(page.getByText('素材预览链接已生成。')).toBeVisible()
  await expect(page.getByRole('link', { name: '打开短时预览' }))
    .toHaveAttribute('href', /preview\.invalid/)

  await page.getByRole('button', { name: '创建下载任务' }).click()
  await expect(page.getByText('下载任务已创建。')).toBeVisible()
  await expect(page.getByRole('cell', { name: 'queued' })).toBeVisible()
  await expect(page.getByRole('cell', { name: '0', exact: true })).toBeVisible()

  await page.getByRole('button', { name: '删除 b04-preview.png' }).click()
  await expect(page.getByText('素材已删除，存储清理任务将异步执行。')).toBeVisible()
  await expect(page.getByRole('cell', { name: 'b04-preview.png', exact: true })).toHaveCount(0)
})
