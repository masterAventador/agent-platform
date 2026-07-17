import { expect, test, type Page } from '@playwright/test'

import { registerAndLogin } from './helpers/auth'
import { queryRuntimeDatabase } from './helpers/runtime-infra'


const toolSuccessOutput = 'Tool call completed in the real worker.'

async function setupExternalToolEmployee(page: Page): Promise<string> {
  const stubPort = process.env.PLAYWRIGHT_RUNTIME_MCP_STUB_PORT ?? '18941'
  const stubBase = `http://127.0.0.1:${stubPort}`
  await page.request.post(`${stubBase}/__control/profile`, { data: { profile: 'v1' } })
  await page.request.post(`${stubBase}/__control/mode`, { data: { mode: 'normal' } })
  await page.request.post(`${stubBase}/__control/auth`, { data: { token: null } })

  // 注册 MCP Server 并同步发现工具
  await page.getByRole('link', { name: '工具与 MCP' }).click()
  await page.getByRole('button', { name: '注册 MCP Server' }).click()
  await page.getByLabel('Server 名称').fill('审批验收 MCP')
  await page.getByLabel('服务地址').fill(`${stubBase}/mcp`)
  await page.getByRole('dialog').getByRole('button', { name: '注册 Server' }).click()
  const serverRow = page.getByRole('row', { name: /审批验收 MCP Streamable HTTP/ })
  await serverRow.getByRole('button', { name: '同步工具' }).click()
  const syncDialog = page.getByRole('dialog')
  await expect(syncDialog.getByText('同步结果')).toBeVisible()
  await syncDialog.getByRole('button', { name: 'Close' }).click()

  // 外部操作风险 → 按策略必须人工审批
  const toolRow = page.getByRole('row', { name: /search_customers 审批验收 MCP/ })
  await toolRow.getByRole('button', { name: /编\s*辑/ }).click()
  const editDialog = page.getByRole('dialog')
  await editDialog.getByLabel('风险等级').click()
  await page.getByText('外部操作', { exact: true }).last().click()
  await editDialog.getByRole('button', { name: '保存修改' }).click()
  await toolRow.getByRole('button', { name: /启\s*用/ }).click()
  await expect(toolRow).toContainText('已启用')

  // tool-call 是 Worker 夹具专用 alias：模型第一轮固定调用 search_customers
  await page.route(/\/api\/v1\/employees$/, async (route) => {
    const request = route.request()
    if (request.method() !== 'POST') {
      await route.continue()
      return
    }
    const payload = request.postDataJSON() as Record<string, unknown>
    await route.continue({
      headers: { ...request.headers(), 'content-type': 'application/json' },
      postData: JSON.stringify({
        ...payload,
        model: { kind: 'gateway_alias', alias: 'tool-call' },
      }),
    })
  })

  await page.getByRole('link', { name: '数字员工' }).click()
  await page.getByRole('button', { name: '创建数字员工' }).click()
  await page.getByLabel('员工名称').fill('审批中心验收专员')
  await page.getByLabel('岗位说明').fill('验证独立审批中心闭环')
  await page.getByLabel('系统指令').fill('查询客户时调用企业搜索工具。')
  await page.getByLabel('Tools').click()
  await page.getByText('审批验收 MCP / search_customers', { exact: true }).last().click()
  await page.keyboard.press('Escape')
  await page.getByRole('button', { name: '保存草稿' }).click()
  await expect(page).toHaveURL(/\/employees\/[0-9a-f-]+$/)
  await page.getByRole('button', { name: '发布员工' }).click()
  await expect(page.getByText('已发布', { exact: true })).toBeVisible()
  return page.url()
}

async function startRunWaitingApproval(page: Page, employeeUrl: string): Promise<string> {
  await page.goto(employeeUrl)
  await page.getByRole('button', { name: '发起任务' }).click()
  await page.getByLabel('任务内容').fill('查询 acme 客户信息')
  await page.getByRole('button', { name: '确认发起' }).click()
  await expect(page).toHaveURL(/\/runs\/[0-9a-f-]+$/)
  await expect(page.getByText('等待审批', { exact: true })).toBeVisible()
  const runId = new URL(page.url()).pathname.split('/').at(-1)
  expect(runId).toMatch(/^[0-9a-f-]{36}$/)
  return runId!
}

// C13：外部风险工具触发审批 → 审批中心待办 → 批准 → 任务继续到终态；越权用户不可见/404。
test('审批中心批准外部工具调用后任务完成，越权用户不可见', async ({ page, browser }) => {
  test.setTimeout(300_000)
  await registerAndLogin(page)
  const employeeUrl = await setupExternalToolEmployee(page)
  const runId = await startRunWaitingApproval(page, employeeUrl)

  // 工作台待审批卡片（C03 条目：C13 完成时新增）
  await page.getByRole('link', { name: '工作台' }).click()
  await expect(page.locator('[aria-label="待审批总数"]')).toContainText('1')
  await page.getByRole('link', { name: '前往审批中心' }).click()
  await expect(page).toHaveURL(/\/approvals$/)

  // 待办可见：工具名、风险、状态
  const pendingRow = page.getByRole('row', { name: /search_customers/ })
  await expect(pendingRow).toBeVisible()
  await expect(pendingRow).toContainText('外部操作')
  await expect(pendingRow).toContainText('待审批')

  const approvalId = queryRuntimeDatabase(
    `select id::text from approvals where run_id='${runId}' and status='pending'`,
  )
  expect(approvalId).toMatch(/^[0-9a-f-]{36}$/)

  // 越权：另一租户用户在自己的工作区看不到该审批，直接访问返回 404
  const strangerContext = await browser.newContext()
  const strangerPage = await strangerContext.newPage()
  await registerAndLogin(strangerPage)
  await strangerPage.getByRole('link', { name: '审批中心', exact: true }).click()
  await expect(strangerPage.getByText('暂无待办审批')).toBeVisible()
  const strangerDetail = await strangerPage.request.get(`/api/v1/approvals/${approvalId}`)
  expect(strangerDetail.status()).toBe(404)
  const strangerApprove = await strangerPage.request.post(
    `/api/v1/approvals/${approvalId}/approve`,
    { data: {} },
  )
  expect(strangerApprove.status()).toBe(404)
  await strangerContext.close()

  // 批准（附理由）→ 待办清空
  await pendingRow.getByRole('button', { name: /批\s*准/ }).click()
  const approveDialog = page.getByRole('dialog')
  await approveDialog.getByLabel('理由').fill('外部检索已获授权')
  await approveDialog.getByRole('button', { name: '确认批准' }).click()
  await expect(page.getByText('暂无待办审批')).toBeVisible()

  // 历史可查且记录批准
  await page.getByRole('tab', { name: '历史' }).click()
  await expect(page.getByRole('row', { name: /search_customers/ })).toContainText('已批准')

  // 任务继续执行到终态，工具真实执行
  await page.goto(`/runs/${runId}`)
  await expect(page.getByText('已完成', { exact: true })).toBeVisible()
  await expect(page.getByText(toolSuccessOutput, { exact: true })).toBeVisible()

  // 可追溯性：审批记录、run 命令、Tool 审计与统一审计留痕
  expect(queryRuntimeDatabase(
    `select status || '|' || coalesce(decision_reason, '') from approvals where id='${approvalId}'`,
  )).toBe('approved|外部检索已获授权')
  expect(queryRuntimeDatabase(
    `select count(*) from run_commands where run_id='${runId}' and action='approve' and processed_at is not null`,
  )).toBe('1')
  expect(Number(queryRuntimeDatabase(
    `select count(*) from tool_audit_events where run_id='${runId}' and event_type='tool.completed' and succeeded`,
  ))).toBeGreaterThan(0)
  expect(queryRuntimeDatabase(
    `select count(*) from audit_events where action='approval.approved' and resource_id='${approvalId}'`,
  )).toBe('1')
})

// C13：拒绝路径——必填理由，拒绝后任务取消、工具不执行。
test('审批中心拒绝后任务取消且工具不执行', async ({ page }) => {
  test.setTimeout(300_000)
  await registerAndLogin(page)
  const employeeUrl = await setupExternalToolEmployee(page)
  const runId = await startRunWaitingApproval(page, employeeUrl)

  await page.getByRole('link', { name: '审批中心', exact: true }).click()
  const pendingRow = page.getByRole('row', { name: /search_customers/ })
  await pendingRow.getByRole('button', { name: /拒\s*绝/ }).click()
  const rejectDialog = page.getByRole('dialog')

  // 理由必填
  await rejectDialog.getByRole('button', { name: '确认拒绝' }).click()
  await expect(rejectDialog.getByText('拒绝审批必须填写理由')).toBeVisible()

  await rejectDialog.getByLabel('理由').fill('外部数据访问未获客户授权')
  await rejectDialog.getByRole('button', { name: '确认拒绝' }).click()
  await expect(page.getByText('暂无待办审批')).toBeVisible()

  await page.goto(`/runs/${runId}`)
  await expect(page.getByText('已取消', { exact: true })).toBeVisible()

  expect(queryRuntimeDatabase(
    `select status || '|' || coalesce(decision_reason, '') from approvals where run_id='${runId}'`,
  )).toBe('rejected|外部数据访问未获客户授权')
  expect(queryRuntimeDatabase(
    `select count(*) from tool_audit_events where run_id='${runId}' and event_type='tool.completed'`,
  )).toBe('0')
})

// C13：超时过期——过期后不可再批准，后台清扫驱动任务不悬挂。
test('过期审批不可批准且任务被系统拒绝收尾', async ({ page }) => {
  test.setTimeout(300_000)
  await registerAndLogin(page)
  const employeeUrl = await setupExternalToolEmployee(page)
  const runId = await startRunWaitingApproval(page, employeeUrl)

  const approvalId = queryRuntimeDatabase(
    `select id::text from approvals where run_id='${runId}' and status='pending'`,
  )
  expect(approvalId).toMatch(/^[0-9a-f-]{36}$/)
  // 时间快进：把过期时间拨到过去（超时判定为真实逻辑，测试只拨时钟）
  queryRuntimeDatabase(
    `update approvals set expires_at = now() - interval '1 minute' where id='${approvalId}'`,
  )

  await page.getByRole('link', { name: '审批中心', exact: true }).click()
  const row = page.getByRole('row', { name: /search_customers/ })
  await expect(row).toContainText('已过期')
  await expect(row.getByRole('button', { name: /批\s*准/ })).toBeDisabled()

  // 服务端拒绝对过期审批的决策（禁止仅前端隐藏）
  const response = await page.request.post(`/api/v1/approvals/${approvalId}/approve`, {
    data: {},
  })
  expect(response.status()).toBe(409)
  // 惰性判定与后台清扫竞争：先到者把记录置为 expired，二者语义一致
  expect(['approval_expired', 'approval_not_pending']).toContain(
    (await response.json()).detail.code,
  )

  // 后台清扫驱动 run 拒绝，任务不会永远悬挂在等待审批
  await page.goto(`/runs/${runId}`)
  await expect(page.getByText('已取消', { exact: true })).toBeVisible({ timeout: 60_000 })
  expect(queryRuntimeDatabase(
    `select status from approvals where id='${approvalId}'`,
  )).toBe('expired')
})
