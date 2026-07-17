import { expect, test, type Browser, type Page } from '@playwright/test'

// 成员页有多张卡片，用较高视口保证展开的角色下拉整体落在视口内，避免选项被推到视口外。
test.use({ viewport: { width: 1400, height: 1800 } })

const PASSWORD = 'correct horse battery staple'

async function registerContext(
  browser: Browser,
  email: string,
): Promise<{ page: Page, close: () => Promise<void> }> {
  const context = await browser.newContext()
  const page = await context.newPage()
  await page.goto('/register')
  await page.getByLabel('邮箱').fill(email)
  await page.getByLabel('密码', { exact: true }).fill(PASSWORD)
  await page.getByLabel('确认密码').fill(PASSWORD)
  await page.getByRole('button', { name: '创建账号' }).click()
  await page.waitForURL(/\/login$/)
  await page.getByLabel('邮箱').fill(email)
  await page.getByLabel('密码', { exact: true }).fill(PASSWORD)
  await page.getByRole('button', { name: /登\s*录/ }).click()
  await page.waitForURL(/\/$/)
  return { page, close: () => context.close() }
}

async function pickRole(page: Page, selectLabel: string, roleLabel: string): Promise<void> {
  // Select 开启 showSearch：聚焦后输入角色名过滤，回车选中过滤后的首个选项。
  // 这样不依赖 AntD 下拉在视口中的定位（表格内下拉常被定位到视口外），稳定可靠。
  const combo = page.getByLabel(selectLabel)
  await combo.click()
  await page.keyboard.type(roleLabel)
  await page.getByRole('option', { name: roleLabel, exact: true }).first().waitFor({ state: 'attached' })
  await page.keyboard.press('Enter')
}

async function inviteMember(
  ownerPage: Page,
  email: string,
  role: 'admin' | 'member',
): Promise<string> {
  await ownerPage.getByRole('link', { name: '企业成员' }).click()
  await expect(ownerPage.getByRole('heading', { name: '企业成员管理' })).toBeVisible()
  await ownerPage.getByLabel('邀请邮箱').fill(email)
  if (role === 'admin') {
    await pickRole(ownerPage, '邀请角色', 'Admin')
  }
  await ownerPage.getByRole('button', { name: '发送邀请' }).click()
  return (await ownerPage.getByTestId('invitation-token').innerText()).trim()
}

async function acceptInvitation(page: Page, token: string): Promise<void> {
  await page.getByRole('link', { name: '账号设置' }).click()
  await page.getByLabel('邀请令牌').fill(token)
  await page.getByRole('button', { name: '接受邀请' }).click()
  await expect(page.getByText('已加入企业，可在左上角切换工作区')).toBeVisible()
}

async function confirmPopconfirm(page: Page): Promise<void> {
  await page.locator('.ant-popconfirm-buttons button.ant-btn-primary').click()
}

test('企业成员：邀请→接受→成员出现→改角色→移除', async ({ browser }) => {
  const stamp = `${Date.now()}-${Math.random().toString(16).slice(2)}`
  const ownerEmail = `e2e-owner-${stamp}@example.com`
  const memberEmail = `e2e-member-${stamp}@example.com`

  const owner = await registerContext(browser, ownerEmail)
  const member = await registerContext(browser, memberEmail)

  const token = await inviteMember(owner.page, memberEmail, 'member')
  await acceptInvitation(member.page, token)

  await owner.page.reload()
  await expect(owner.page.getByRole('cell', { name: memberEmail })).toBeVisible()

  await pickRole(owner.page, `${memberEmail} 角色`, 'Admin')
  await expect(owner.page.getByText('成员角色已更新')).toBeVisible()

  await owner.page
    .getByRole('row', { name: new RegExp(memberEmail) })
    .getByRole('button', { name: /移\s*除/ })
    .click()
  await confirmPopconfirm(owner.page)
  await expect(owner.page.getByText('成员已移除')).toBeVisible()
  await expect(owner.page.getByRole('cell', { name: memberEmail })).toHaveCount(0)

  await owner.close()
  await member.close()
})

test('企业成员：Owner 转移后原 Owner 失去成员管理权', async ({ browser }) => {
  const stamp = `${Date.now()}-${Math.random().toString(16).slice(2)}`
  const ownerEmail = `e2e-owner2-${stamp}@example.com`
  const adminEmail = `e2e-admin2-${stamp}@example.com`

  const owner = await registerContext(browser, ownerEmail)
  const admin = await registerContext(browser, adminEmail)

  const token = await inviteMember(owner.page, adminEmail, 'admin')
  await acceptInvitation(admin.page, token)

  await owner.page.reload()
  await expect(owner.page.getByRole('cell', { name: adminEmail })).toBeVisible()

  await owner.page
    .getByRole('row', { name: new RegExp(adminEmail) })
    .getByRole('button', { name: '转为 Owner' })
    .click()
  await confirmPopconfirm(owner.page)
  await expect(owner.page.getByText('企业所有权已转移')).toBeVisible()

  await owner.page.reload()
  await expect(owner.page.getByRole('link', { name: '企业成员' })).toHaveCount(0)

  await admin.page.reload()
  await expect(admin.page.getByRole('link', { name: '企业成员' })).toBeVisible()

  await owner.close()
  await admin.close()
})

test('企业成员：非 Owner 调用成员管理接口被服务端拒绝 403', async ({ browser }) => {
  const stamp = `${Date.now()}-${Math.random().toString(16).slice(2)}`
  const ownerEmail = `e2e-owner3-${stamp}@example.com`
  const memberEmail = `e2e-member3-${stamp}@example.com`

  const owner = await registerContext(browser, ownerEmail)
  const member = await registerContext(browser, memberEmail)

  const token = await inviteMember(owner.page, memberEmail, 'member')
  await acceptInvitation(member.page, token)

  const ownerTenant = (await owner.page.request.get('/api/v1/auth/me')).json()
  const tenantId = (await ownerTenant).workspaces[0].id as string

  // member 前端看不到企业成员入口
  await member.page.goto('/')
  await expect(member.page.getByRole('link', { name: '企业成员' })).toHaveCount(0)

  // 服务端资源级授权：member 直接调用改角色接口返回 403
  const forbidden = await member.page.request.patch(
    `/api/v1/tenant/members/${crypto.randomUUID()}/role`,
    { headers: { 'X-Tenant-ID': tenantId }, data: { role: 'admin' } },
  )
  expect(forbidden.status()).toBe(403)

  await owner.close()
  await member.close()
})
