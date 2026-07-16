import { expect, test } from '@playwright/test'

import { registerAndLogin } from './helpers/auth'


test('用户可以注册 MCP Server、登记工具并绑定到数字员工', async ({ page }) => {
  test.setTimeout(60_000)
  await registerAndLogin(page)

  await page.getByRole('link', { name: '工具与 MCP' }).click()
  await expect(page).toHaveURL(/\/tools$/)
  await expect(page.getByText('还没有 MCP Server')).toBeVisible()

  await page.getByRole('button', { name: '注册 MCP Server' }).click()
  await page.getByLabel('Server 名称').fill('企业搜索 MCP')
  await page.getByLabel('传输方式').click()
  await page.getByText('stdio', { exact: true }).last().click()
  await expect(page.getByLabel('启动命令')).toBeVisible()
  await expect(page.getByLabel('服务地址')).not.toBeVisible()

  await page.getByLabel('传输方式').click()
  await page.getByText('Streamable HTTP', { exact: true }).last().click()
  await page.getByLabel('服务地址').fill('https://mcp.example.com/api')
  await page.getByLabel('凭据引用').fill('vault://tenants/acme/mcp/search')
  await page.getByRole('dialog').getByRole('button', { name: '注册 Server' }).click()

  await expect(page.getByText('企业搜索 MCP', { exact: true })).toBeVisible()
  await expect(page.getByText('凭据已配置')).toBeVisible()
  await expect(page.getByText('vault://tenants/acme/mcp/search')).toHaveCount(0)

  await page.getByRole('button', { name: '登记 Tool' }).click()
  const toolDialog = page.getByRole('dialog')
  await toolDialog.getByLabel('所属 Server').click()
  await page.getByText('企业搜索 MCP', { exact: true }).last().click()
  await toolDialog.getByLabel('Tool 名称').fill('search_customers')
  await toolDialog.getByLabel('说明').fill('在企业客户系统中搜索客户')
  await toolDialog.getByLabel('输入 JSON Schema').fill('{')
  await toolDialog.getByRole('button', { name: '登记 Tool' }).click()
  await expect(toolDialog.getByText('请输入有效的 JSON 对象')).toBeVisible()
  await toolDialog.getByLabel('输入 JSON Schema').fill(
    JSON.stringify({
      type: 'object',
      properties: { query: { type: 'string' } },
      required: ['query'],
    }),
  )
  await toolDialog.getByLabel('风险等级').click()
  await page.getByText('外部操作', { exact: true }).last().click()
  await toolDialog.getByRole('button', { name: '登记 Tool' }).click()

  const toolRow = page.getByRole('row', { name: /search_customers/ })
  await expect(toolRow).toContainText('外部操作')
  await toolRow.getByRole('button', { name: /禁\s*用/ }).click()
  await expect(toolRow).toContainText('已禁用')
  await toolRow.getByRole('button', { name: /启\s*用/ }).click()
  await expect(toolRow).toContainText('已启用')

  const serverRow = page.getByRole('row', { name: /企业搜索 MCP Streamable HTTP/ })
  await serverRow.getByRole('button', { name: /禁\s*用/ }).click()
  await expect(serverRow).toContainText('已禁用')

  await page.getByRole('link', { name: '数字员工' }).click()
  await page.getByRole('button', { name: '创建数字员工' }).click()
  await page.getByLabel('Tools').click()
  await expect(page.getByText('企业搜索 MCP / search_customers', { exact: true })).toHaveCount(0)
  await page.keyboard.press('Escape')

  await page.getByRole('link', { name: '工具与 MCP' }).click()
  await serverRow.getByRole('button', { name: /启\s*用/ }).click()
  await expect(serverRow).toContainText('已启用')

  await page.getByRole('link', { name: '数字员工' }).click()
  await page.getByRole('button', { name: '创建数字员工' }).click()
  await page.getByLabel('员工名称').fill('客户研究专员')
  await page.getByLabel('岗位说明').fill('负责企业客户研究')
  await page.getByLabel('系统指令').fill('使用企业搜索工具核验客户信息。')
  await page.getByLabel('Tools').click()
  await page.getByText('企业搜索 MCP / search_customers', { exact: true }).last().click()
  await page.keyboard.press('Escape')
  await page.getByRole('button', { name: '保存草稿' }).click()

  await expect(page).toHaveURL(/\/employees\/[0-9a-f-]+$/)
  await page.getByRole('button', { name: /编\s*辑/ }).click()
  await expect(page.getByText('企业搜索 MCP / search_customers', { exact: true })).toBeVisible()
  await page.getByLabel('岗位说明').fill('负责企业客户研究和信息核验')
  await page.getByRole('button', { name: '保存草稿' }).click()
  await page.getByRole('button', { name: /编\s*辑/ }).click()
  await expect(page.getByText('企业搜索 MCP / search_customers', { exact: true })).toBeVisible()
})

// C09：真实本地 MCP stub 上的完整生命周期（连接测试 → 自动发现同步 → 差异 → 凭据 → 脱敏）。
// 该用例独占 stub 的全局控制状态（profile/mode/auth），必须保持单用例串行使用。
test('用户可以对真实 MCP Server 完成连接测试、自动发现同步、差异查看与凭据配置', async ({ page }) => {
  test.setTimeout(120_000)
  const stubPort = process.env.PLAYWRIGHT_MCP_STUB_PORT ?? '18940'
  const stubBase = `http://127.0.0.1:${stubPort}`
  const secretToken = 'e2e-secret-token-never-echo'
  const resetStub = async () => {
    await page.request.post(`${stubBase}/__control/profile`, { data: { profile: 'v1' } })
    await page.request.post(`${stubBase}/__control/mode`, { data: { mode: 'normal' } })
    await page.request.post(`${stubBase}/__control/auth`, { data: { token: null } })
  }
  await resetStub()

  await registerAndLogin(page)
  await page.getByRole('link', { name: '工具与 MCP' }).click()
  await expect(page).toHaveURL(/\/tools$/)

  // 注册指向本地 stub 的 MCP Server
  await page.getByRole('button', { name: '注册 MCP Server' }).click()
  await page.getByLabel('Server 名称').fill('本地验收 MCP')
  await page.getByLabel('服务地址').fill(`${stubBase}/mcp`)
  await page.getByRole('dialog').getByRole('button', { name: '注册 Server' }).click()
  const serverRow = page.getByRole('row', { name: /本地验收 MCP Streamable HTTP/ })
  await expect(serverRow).toContainText('未测试')

  // 连接测试
  await serverRow.getByRole('button', { name: '测试连接' }).click()
  await expect(serverRow).toContainText('连接正常（发现 2 个工具）')

  // 自动发现同步 + 差异展示
  await serverRow.getByRole('button', { name: '同步工具' }).click()
  const syncDialog = page.getByRole('dialog')
  await expect(syncDialog.getByText('同步结果')).toBeVisible()
  await expect(syncDialog.getByText('search_customers')).toBeVisible()
  await expect(syncDialog.getByText('send_notification')).toBeVisible()
  await syncDialog.getByRole('button', { name: 'Close' }).click()

  const discoveredRow = page.getByRole('row', { name: /search_customers/ })
  await expect(discoveredRow).toContainText('自动发现')
  await expect(discoveredRow).toContainText('已禁用')
  await expect(discoveredRow).toContainText('外部操作')

  // 上游目录变化：v2 目录 移除 send_notification、变更 search_customers、新增 fetch_order
  await page.request.post(`${stubBase}/__control/profile`, { data: { profile: 'v2' } })
  await serverRow.getByRole('button', { name: '同步工具' }).click()
  const secondSyncDialog = page.getByRole('dialog')
  await expect(secondSyncDialog.getByText('同步结果')).toBeVisible()
  await expect(secondSyncDialog.getByText('fetch_order')).toBeVisible()
  await expect(secondSyncDialog.getByText('上游移除', { exact: true })).toBeVisible()
  await expect(secondSyncDialog.getByText('send_notification')).toBeVisible()
  await secondSyncDialog.getByRole('button', { name: 'Close' }).click()
  await expect(page.getByRole('row', { name: /send_notification/ })).toContainText('上游已移除')

  // 凭据链路：stub 开启鉴权后连接失败，配置凭据后恢复，任何页面不回显明文
  await page.request.post(`${stubBase}/__control/auth`, { data: { token: secretToken } })
  await serverRow.getByRole('button', { name: '测试连接' }).click()
  await expect(serverRow).toContainText(/连接失败（mcp_/)

  await serverRow.getByRole('button', { name: '配置凭据' }).click()
  const credentialsDialog = page.getByRole('dialog')
  await expect(credentialsDialog.getByLabel('Header 名称')).toHaveValue('Authorization')
  await credentialsDialog.getByLabel('凭据值').fill(`Bearer ${secretToken}`)
  await credentialsDialog.getByRole('button', { name: '保存凭据' }).click()
  await expect(serverRow).toContainText('凭据已配置')

  await serverRow.getByRole('button', { name: '测试连接' }).click()
  await expect(serverRow).toContainText('连接正常（发现 2 个工具）')
  expect(await page.content()).not.toContain(secretToken)

  await resetStub()
})
