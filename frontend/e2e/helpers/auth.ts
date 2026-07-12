import type { Page } from '@playwright/test'


export async function registerAndLogin(page: Page): Promise<string> {
  const email = `e2e-shell-${Date.now()}-${Math.random().toString(16).slice(2)}@example.com`
  const password = 'correct horse battery staple'

  await page.goto('/register')
  await page.getByLabel('邮箱').fill(email)
  await page.getByLabel('密码', { exact: true }).fill(password)
  await page.getByLabel('确认密码').fill(password)
  await page.getByRole('button', { name: '创建账号' }).click()
  await page.waitForURL(/\/login$/)
  await page.getByLabel('密码', { exact: true }).fill(password)
  await page.getByRole('button', { name: /登\s*录/ }).click()
  await page.waitForURL(/\/$/)

  return email
}
