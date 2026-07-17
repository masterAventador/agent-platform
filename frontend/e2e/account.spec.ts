import { expect, test, type Browser, type Page } from '@playwright/test'

const PASSWORD = 'correct horse battery staple'

async function registerContext(
  browser: Browser,
  email: string,
  password: string = PASSWORD,
): Promise<{ page: Page, close: () => Promise<void> }> {
  const context = await browser.newContext()
  const page = await context.newPage()
  await page.goto('/register')
  await page.getByLabel('邮箱').fill(email)
  await page.getByLabel('密码', { exact: true }).fill(password)
  await page.getByLabel('确认密码').fill(password)
  await page.getByRole('button', { name: '创建账号' }).click()
  await page.waitForURL(/\/login$/)
  await page.getByLabel('邮箱').fill(email)
  await page.getByLabel('密码', { exact: true }).fill(password)
  await page.getByRole('button', { name: /登\s*录/ }).click()
  await page.waitForURL(/\/$/)
  return { page, close: () => context.close() }
}

async function loginContext(
  browser: Browser,
  email: string,
  password: string,
): Promise<{ page: Page, close: () => Promise<void> }> {
  const context = await browser.newContext()
  const page = await context.newPage()
  await page.goto('/login')
  await page.getByLabel('邮箱').fill(email)
  await page.getByLabel('密码', { exact: true }).fill(password)
  await page.getByRole('button', { name: /登\s*录/ }).click()
  await page.waitForURL(/\/$/)
  return { page, close: () => context.close() }
}

test('账号：编辑资料并完成邮箱验证', async ({ browser }) => {
  const email = `e2e-account-${Date.now()}-${Math.random().toString(16).slice(2)}@example.com`
  const { page, close } = await registerContext(browser, email)

  await page.getByRole('link', { name: '账号设置' }).click()
  await expect(page.getByRole('heading', { name: '账号设置' })).toBeVisible()
  await expect(page.getByText('未验证')).toBeVisible()

  await page.getByLabel('昵称').fill('自动化用户')
  await page.getByRole('button', { name: /保\s*存/ }).first().click()
  await expect(page.getByText('资料已更新')).toBeVisible()

  await page.getByRole('button', { name: '发送邮箱验证' }).click()
  // Demo 通道把验证令牌回填到输入框
  const token = await page.getByLabel('邮箱验证令牌').inputValue()
  expect(token.length).toBeGreaterThan(0)
  await page.getByRole('button', { name: '确认验证' }).click()
  await expect(page.getByText('已验证', { exact: true })).toBeVisible()
  await expect(page.getByText('未验证')).toHaveCount(0)

  await close()
})

test('账号：修改密码后其它设备会话立即失效', async ({ browser }) => {
  const email = `e2e-pwd-${Date.now()}-${Math.random().toString(16).slice(2)}@example.com`
  const first = await registerContext(browser, email)
  const second = await loginContext(browser, email, PASSWORD)

  const newPassword = 'a brand new strong passphrase'
  await first.page.getByRole('link', { name: '账号设置' }).click()
  await first.page.getByLabel('当前密码').fill(PASSWORD)
  await first.page.getByLabel('新密码').fill(newPassword)
  await first.page.getByRole('button', { name: '修改密码' }).click()
  await expect(first.page.getByText('密码已修改，其它设备的登录已失效')).toBeVisible()

  // 第二个设备的旧会话失效：刷新后被踢回登录页
  await second.page.reload()
  await expect(second.page).toHaveURL(/\/login$/)

  // 当前设备仍然可用
  await first.page.reload()
  await expect(first.page.getByRole('heading', { name: 'AI 数字员工平台' })).toBeVisible()

  await first.close()
  await second.close()
})

test('账号：找回密码闭环并用新密码登录', async ({ browser }) => {
  const email = `e2e-reset-${Date.now()}-${Math.random().toString(16).slice(2)}@example.com`
  const { page, close } = await registerContext(browser, email)
  // 退出登录，进入找回密码
  await page.getByRole('button', { name: '退出登录' }).click()
  await page.waitForURL(/\/login$/)

  await page.getByRole('link', { name: '忘记密码？' }).click()
  await expect(page.getByRole('heading', { name: '找回密码' })).toBeVisible()
  await page.getByLabel('邮箱').fill(email)
  await page.getByRole('button', { name: '发送重置请求' }).click()
  await expect(page.getByText('若该邮箱已注册，我们已生成重置令牌')).toBeVisible()

  // 模拟邮件链接：通过受控开发通道取回重置令牌
  const devToken = await page.request.get('/api/v1/account/password-reset/dev-token', {
    params: { email },
  })
  expect(devToken.status()).toBe(200)
  const token = (await devToken.json()).token as string

  const newPassword = 'recovered even stronger pass'
  await page.getByLabel('重置令牌').fill(token)
  await page.getByLabel('新密码').fill(newPassword)
  await page.getByRole('button', { name: '重置密码' }).click()
  await page.waitForURL(/\/login$/)

  // 旧密码失效、新密码可登录
  await page.getByLabel('邮箱').fill(email)
  await page.getByLabel('密码', { exact: true }).fill(PASSWORD)
  await page.getByRole('button', { name: /登\s*录/ }).click()
  await expect(page.getByText('邮箱或密码错误')).toBeVisible()

  await page.getByLabel('邮箱').fill(email)
  await page.getByLabel('密码', { exact: true }).fill(newPassword)
  await page.getByRole('button', { name: /登\s*录/ }).click()
  await page.waitForURL(/\/$/)

  await close()
})

test('账号：登录设备列表可撤销单个会话', async ({ browser }) => {
  const email = `e2e-session-${Date.now()}-${Math.random().toString(16).slice(2)}@example.com`
  const first = await registerContext(browser, email)
  const second = await loginContext(browser, email, PASSWORD)

  await first.page.getByRole('link', { name: '账号设置' }).click()
  await expect(first.page.getByText('当前设备')).toBeVisible()
  // 存在另一台活跃设备，撤销它
  await first.page.getByRole('button', { name: /撤\s*销/ }).first().click()
  await first.page.locator('.ant-popconfirm-buttons button.ant-btn-primary').click()
  await expect(first.page.getByText('会话已撤销')).toBeVisible()

  await second.page.reload()
  await expect(second.page).toHaveURL(/\/login$/)

  await first.close()
  await second.close()
})
