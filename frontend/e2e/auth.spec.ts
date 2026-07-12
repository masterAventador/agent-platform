import { expect, test } from '@playwright/test'

test('用户可以注册、登录、刷新恢复会话并退出', async ({ page }) => {
  const email = `e2e-auth-${Date.now()}@example.com`
  const password = 'correct horse battery staple'

  await page.goto('/')
  await expect(page).toHaveURL(/\/login$/)

  await page.getByRole('link', { name: '注册账号' }).click()
  await page.getByLabel('邮箱').fill(email)
  await page.getByLabel('密码', { exact: true }).fill(password)
  await page.getByLabel('确认密码').fill(password)
  await page.getByRole('button', { name: '创建账号' }).click()

  await expect(page).toHaveURL(/\/login$/)
  await expect(page.getByText('注册成功，请登录')).toBeVisible()

  await page.getByLabel('邮箱').fill(email)
  await page.getByLabel('密码', { exact: true }).fill(password)
  await page.getByRole('button', { name: /登\s*录/ }).click()

  await expect(page).toHaveURL(/\/$/)
  await expect(page.getByRole('heading', { name: 'AI 数字员工平台' })).toBeVisible()
  await expect(page.getByText(email)).toBeVisible()

  await page.reload()
  await expect(page.getByRole('heading', { name: 'AI 数字员工平台' })).toBeVisible()

  await page.getByRole('button', { name: '退出登录' }).click()
  await expect(page).toHaveURL(/\/login$/)
  await expect(page.getByRole('heading', { name: '登录' })).toBeVisible()
})
