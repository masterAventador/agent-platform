import { expect, type Page } from '@playwright/test'

export const demoEmail = 'demo@example.com'
export const demoPassword = 'agent-platform-demo'

export async function loginWithDemoAccount(page: Page): Promise<string> {
  await page.goto('/login')
  await page.getByLabel('邮箱').fill(demoEmail)
  await page.getByLabel('密码', { exact: true }).fill(demoPassword)
  await page.getByRole('button', { name: /登\s*录/ }).click()
  await page.waitForURL(/\/$/)

  return demoEmail
}


export async function registerAndLogin(page: Page): Promise<string> {
  const email = `e2e-shell-${Date.now()}-${Math.random().toString(16).slice(2)}@example.com`
  const password = 'correct horse battery staple'

  await page.goto('/register')
  await page.getByLabel('邮箱').fill(email)
  await page.getByLabel('密码', { exact: true }).fill(password)
  await page.getByLabel('确认密码').fill(password)
  await page.getByRole('button', { name: '创建账号' }).click()
  await page.waitForURL(/\/login$/)
  await page.getByRole('heading', { name: '登录' }).waitFor()
  const passwordInput = page.getByLabel('密码', { exact: true })
  await passwordInput.fill(password)
  await expect(passwordInput).toHaveValue(password)
  await page.getByRole('button', { name: /登\s*录/ }).click()
  await page.waitForURL(/\/$/)

  return email
}
