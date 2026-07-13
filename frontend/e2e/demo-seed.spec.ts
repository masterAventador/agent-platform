import { expect, test } from '@playwright/test'


const demoEmail = 'demo@example.com'
const demoPassword = 'agent-platform-demo'
const demoWorkspaceName = 'Agent Platform 演示工作区'

test('用户可以登录 Demo Seed 并从页面查看代表性数据', async ({ page }) => {
  await page.goto('/login')
  await page.getByLabel('邮箱').fill(demoEmail)
  await page.getByLabel('密码', { exact: true }).fill(demoPassword)
  await page.getByRole('button', { name: /登\s*录/ }).click()

  await expect(page).toHaveURL(/\/$/)
  await expect(page.getByText(demoEmail, { exact: true })).toBeVisible()
  await expect(page.getByLabel('当前工作区').locator('..')).toContainText(demoWorkspaceName)

  await page.getByRole('link', { name: '数字员工' }).click()
  await expect(page.getByRole('heading', { name: '数字员工' })).toBeVisible()
  await expect(page.getByText('还没有数字员工')).toHaveCount(0)
  await expect(page.getByText('演示研究助理', { exact: true })).toBeVisible()
  await expect(page.getByText('已发布', { exact: true }).first()).toBeVisible()

  await page.getByRole('link', { name: '任务中心' }).click()
  await expect(page.getByRole('heading', { name: '任务中心' })).toBeVisible()
  await expect(page.getByText('还没有任务')).toHaveCount(0)
  await expect(page.getByText(/^任务 [0-9a-f]{8}$/).first()).toBeVisible()
  await expect(page.getByText(/数字员工版本 \d+ · /).first()).toBeVisible()
  await expect(page.getByText('已完成', { exact: true })).toBeVisible()
  await expect(page.getByText('失败', { exact: true })).toBeVisible()

  await page.getByRole('link', { name: '工具与 MCP' }).click()
  await expect(page.getByRole('heading', { name: '工具与 MCP' })).toBeVisible()
  await expect(page.getByText('演示企业搜索（未启用）', { exact: true })).toBeVisible()
  await expect(page.getByText('search_demo_documents', { exact: true })).toBeVisible()

  await page.getByRole('link', { name: '任务运维' }).click()
  await expect(page.getByRole('heading', { name: '死信管理' })).toBeVisible()
  await expect(page.getByText('DemoDeliveryFailure', { exact: true })).toBeVisible()
})
