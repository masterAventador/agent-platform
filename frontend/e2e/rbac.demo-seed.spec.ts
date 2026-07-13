import { expect, type Page, test } from '@playwright/test'


const demoPassword = 'agent-platform-demo'
const demoWorkspaceName = 'Agent Platform 演示工作区'
const managerPermissions = [
  'employees.manage',
  'knowledge.manage',
  'operations.manage',
  'runs.execute',
  'runs.manage',
  'skills.manage',
  'tools.manage',
]

interface LoginWorkspace {
  id: string
  name: string
  role: 'owner' | 'admin' | 'member'
  permissions: string[]
}

async function login(page: Page, email: string): Promise<LoginWorkspace> {
  await page.goto('/login')
  await page.getByLabel('邮箱').fill(email)
  await page.getByLabel('密码', { exact: true }).fill(demoPassword)
  const [response] = await Promise.all([
    page.waitForResponse((candidate) => (
      candidate.url().endsWith('/api/v1/auth/login')
      && candidate.request().method() === 'POST'
    )),
    page.getByRole('button', { name: /登\s*录/ }).click(),
  ])
  expect(response.status()).toBe(200)
  await expect(page).toHaveURL(/\/$/)
  const user = await response.json() as { workspaces: LoginWorkspace[] }
  return user.workspaces[0]!
}

async function expectManagerSurfaces(page: Page) {
  await expect(page.getByRole('link', { name: '工具与 MCP' })).toBeVisible()
  await expect(page.getByRole('link', { name: '任务运维' })).toBeVisible()

  await page.getByRole('link', { name: '数字员工' }).click()
  await expect(page.getByRole('button', { name: '创建数字员工' })).toBeVisible()

  await page.getByRole('link', { name: '知识库' }).click()
  await expect(page.getByRole('button', { name: '创建知识库' })).toBeVisible()

  await page.getByRole('link', { name: 'Skill 中心' }).click()
  await expect(page.getByRole('button', { name: '上传 Skill' })).toBeVisible()

  await page.goto('/employees/new')
  await expect(page.getByRole('heading', { name: '创建数字员工' })).toBeVisible()
}

async function mockKnowledgeBase(page: Page) {
  await page.route('**/api/v1/knowledge-bases**', async (route) => {
    const request = route.request()
    const pathname = new URL(request.url()).pathname
    if (request.method() === 'GET' && pathname.endsWith('/knowledge-bases')) {
      await route.fulfill({
        json: [{
          id: '00000000-0000-4000-8000-000000000099',
          tenant_id: '00000000-0000-4000-8000-000000000001',
          name: '权限验收知识库',
          description: '仅用于前端 RBAC 浏览器验收',
          provider: 'ragflow',
        }],
      })
      return
    }
    if (request.method() === 'GET' && pathname.endsWith('/documents')) {
      await route.fulfill({ json: [] })
      return
    }
    if (
      request.method() === 'DELETE'
      && pathname.endsWith('/00000000-0000-4000-8000-000000000099')
    ) {
      await route.fulfill({ status: 204 })
      return
    }
    await route.continue()
  })
}

test('owner 权限响应、管理入口与知识库删除闭环一致', async ({ page }) => {
  await mockKnowledgeBase(page)
  const workspace = await login(page, 'demo@example.com')

  expect(workspace).toMatchObject({ name: demoWorkspaceName, role: 'owner' })
  expect(workspace.permissions).toEqual([...managerPermissions, 'workspace.manage'])
  await expectManagerSurfaces(page)

  await page.goto('/knowledge-bases')
  await page.getByText('权限验收知识库', { exact: true }).click()
  await page.getByRole('button', { name: '删除知识库' }).click()
  const confirmation = page.getByRole('dialog', { name: '确认删除知识库' })
  const deleteRequest = page.waitForRequest((request) => (
    request.method() === 'DELETE'
    && request.url().includes('/00000000-0000-4000-8000-000000000099')
  ))
  await confirmation.getByRole('button', { name: '确认删除' }).click()
  await deleteRequest
  await expect(page).toHaveURL(/\/knowledge-bases$/)
})

test('admin 获得业务管理权限但没有 workspace.manage', async ({ page }) => {
  const workspace = await login(page, 'demo.admin@example.com')

  expect(workspace).toMatchObject({ name: demoWorkspaceName, role: 'admin' })
  expect(workspace.permissions).toEqual(managerPermissions)
  await expectManagerSurfaces(page)

  await page.goto('/employees')
  await expect(page.getByText('演示私有草稿员工', { exact: true })).toBeVisible()
})

test('member 保留只读与任务执行，所有管理入口和直达路由受控拒绝', async ({ page }) => {
  const ownerWorkspace = await login(page, 'demo@example.com')
  const ownerRunsResponse = await page.request.get('/api/v1/runs', {
    headers: { 'X-Tenant-ID': ownerWorkspace.id },
  })
  expect(ownerRunsResponse.status()).toBe(200)
  const ownerRuns = await ownerRunsResponse.json() as Array<{ id: string; status: string }>
  const ownerFailedRun = ownerRuns.find((run) => run.status === 'failed')
  expect(ownerFailedRun).toBeDefined()

  await page.getByRole('button', { name: '退出登录' }).click()
  await expect(page).toHaveURL(/\/login$/)
  const workspace = await login(page, 'demo.member@example.com')

  expect(workspace).toMatchObject({ name: demoWorkspaceName, role: 'member' })
  expect(workspace.permissions).toEqual(['runs.execute'])

  await page.getByRole('link', { name: '任务中心' }).click()
  await expect(page.getByText('已完成', { exact: true })).toBeVisible()
  await expect(page.getByText('失败', { exact: true })).toHaveCount(0)

  const forbiddenRunResponse = await page.request.get(`/api/v1/runs/${ownerFailedRun!.id}`, {
    headers: { 'X-Tenant-ID': workspace.id },
  })
  expect(forbiddenRunResponse.status()).toBe(404)
  await page.goto(`/runs/${ownerFailedRun!.id}`)
  await expect(page.getByText('任务不存在或无权访问')).toBeVisible()

  const forbiddenKnowledgeResponse = await page.request.post('/api/v1/knowledge-bases', {
    headers: { 'X-Tenant-ID': workspace.id },
    data: { name: 'member 不应创建', description: '权限验收' },
  })
  expect(forbiddenKnowledgeResponse.status()).toBe(403)

  await mockKnowledgeBase(page)
  await expect(page.getByRole('link', { name: '工具与 MCP' })).toHaveCount(0)
  await expect(page.getByRole('link', { name: '任务运维' })).toHaveCount(0)

  await page.getByRole('link', { name: '数字员工' }).click()
  await expect(page.getByText('演示研究助理', { exact: true })).toBeVisible()
  await expect(page.getByText('演示私有草稿员工', { exact: true })).toHaveCount(0)
  await expect(page.getByRole('button', { name: '创建数字员工' })).toHaveCount(0)
  await page.getByText('演示研究助理', { exact: true }).click()
  await expect(page.getByRole('button', { name: '发起任务' })).toBeVisible()
  await expect(page.getByRole('button', { name: /编\s*辑/ })).toHaveCount(0)

  await page.goto('/employees/new')
  await expect(page.getByText('无权编辑数字员工')).toBeVisible()
  await page.goto('/tools')
  await expect(page.getByText('无权访问工具与 MCP')).toBeVisible()
  await page.goto('/operations/dead-letters')
  await expect(page.getByText('无权访问死信管理')).toBeVisible()

  await page.goto('/knowledge-bases')
  await expect(page.getByRole('button', { name: '创建知识库' })).toHaveCount(0)
  await page.getByText('权限验收知识库', { exact: true }).click()
  await expect(page.getByRole('button', { name: '删除知识库' })).toHaveCount(0)
  await expect(page.getByLabel('选择文档')).toHaveCount(0)

  await page.getByRole('link', { name: 'Skill 中心' }).click()
  await expect(page.getByRole('button', { name: '上传 Skill' })).toHaveCount(0)
})
