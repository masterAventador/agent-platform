import { expect, test, type Page } from '@playwright/test'

import { registerAndLogin } from './helpers/auth'
import { queryRuntimeDatabase } from './helpers/runtime-infra'


const expectedOutput = process.env.PLAYWRIGHT_RUNTIME_EXPECTED_OUTPUT
  ?? 'Runtime E2E completed in the real worker.'

const AGENT_ONLY_GRAPH = JSON.stringify({
  entrypoint: 'answer',
  nodes: [{ name: 'answer', type: 'agent', config: { prompt: '答复用户请求' }, next: null }],
})

const APPROVAL_GRAPH = JSON.stringify({
  entrypoint: 'collect',
  nodes: [
    { name: 'collect', type: 'agent', config: { prompt: '整理请求' }, next: 'review' },
    { name: 'review', type: 'human_approval', config: { title: '请审批' }, next: 'finish' },
    { name: 'finish', type: 'agent', config: { prompt: '输出最终答复' }, next: null },
  ],
})

async function registerAndPublishWorkflow(
  page: Page,
  name: string,
  graphJson: string,
): Promise<void> {
  await page.getByRole('link', { name: '工作流中心' }).click()
  await expect(page).toHaveURL(/\/workflows$/)
  await page.getByLabel('工作流名称').fill(name)
  await page.getByLabel('工作流图（JSON）').fill(graphJson)
  await page.getByRole('button', { name: '注册工作流' }).click()
  const card = page.locator('.ant-card', { hasText: name })
  await expect(card).toBeVisible()
  await card.getByRole('button', { name: '查看版本' }).click()
  await card.getByRole('button', { name: '发布 v1' }).click()
  await expect(card.getByText('已发布 v1')).toBeVisible()
}

async function createAndPublishWorkflowEmployee(
  page: Page,
  employeeName: string,
  workflowName: string,
  workMode: '固定流程' | '混合协作',
): Promise<string> {
  await page.getByRole('link', { name: '数字员工' }).click()
  await page.getByRole('button', { name: '创建数字员工' }).click()
  await page.getByLabel('员工名称').fill(employeeName)
  await page.getByLabel('岗位说明').fill('运行时验收流程员工')
  await page.getByLabel('工作模式').click()
  await page.getByRole('option', { name: workMode }).click()
  await page.getByLabel('引用工作流').click()
  await page.getByRole('option', { name: new RegExp(`${workflowName}（v1）`) }).click()
  await page.getByLabel('系统指令').fill('按已发布工作流执行')
  await page.getByRole('button', { name: '保存草稿' }).click()
  await expect(page).toHaveURL(/\/employees\/[0-9a-f-]+$/)
  await page.getByRole('button', { name: '发布员工' }).click()
  await expect(page.getByText('已发布', { exact: true })).toBeVisible()
  return page.url()
}

test('流程数字员工通过真实 LangGraph 工作流编排跑到终态', async ({ page }) => {
  test.setTimeout(300_000)
  await registerAndLogin(page)

  const workflowName = `运行时工作流 ${Date.now()}`
  await registerAndPublishWorkflow(page, workflowName, AGENT_ONLY_GRAPH)
  const employeeUrl = await createAndPublishWorkflowEmployee(
    page,
    `运行时流程员工 ${Date.now()}`,
    workflowName,
    '固定流程',
  )

  await page.goto(employeeUrl)
  await page.getByRole('button', { name: '发起任务' }).click()
  await page.getByLabel('任务内容').fill('执行固定工作流端到端验收')
  await page.getByRole('button', { name: '确认发起' }).click()

  await expect(page).toHaveURL(/\/runs\/[0-9a-f-]+$/)
  await expect(page.getByText('已完成', { exact: true })).toBeVisible()
  await expect(page.getByText('模型输出', { exact: true })).toBeVisible()
  await expect(page.getByText(expectedOutput, { exact: true })).toBeVisible()

  const runId = new URL(page.url()).pathname.split('/').at(-1)
  expect(runId).toMatch(/^[0-9a-f-]{36}$/)
  const leaseStatus = queryRuntimeDatabase(
    `SELECT status FROM sandbox_leases WHERE run_id = '${runId}'`,
  )
  expect(leaseStatus).toBe('deleted')
})

test('含人工审批节点的工作流经审批中心批准后继续到终态', async ({ page }) => {
  test.setTimeout(300_000)
  await registerAndLogin(page)

  const workflowName = `审批工作流 ${Date.now()}`
  await registerAndPublishWorkflow(page, workflowName, APPROVAL_GRAPH)
  const employeeUrl = await createAndPublishWorkflowEmployee(
    page,
    `审批流程员工 ${Date.now()}`,
    workflowName,
    '固定流程',
  )

  await page.goto(employeeUrl)
  await page.getByRole('button', { name: '发起任务' }).click()
  await page.getByLabel('任务内容').fill('触发工作流人工审批节点')
  await page.getByRole('button', { name: '确认发起' }).click()
  await expect(page).toHaveURL(/\/runs\/[0-9a-f-]+$/)
  await expect(page.getByText('等待审批', { exact: true })).toBeVisible()
  const runId = new URL(page.url()).pathname.split('/').at(-1)!

  // 人工审批节点的 Interrupt 复用 C13 审批中心。
  const approvalId = queryRuntimeDatabase(
    `SELECT id::text FROM approvals WHERE run_id='${runId}' AND status='pending'`,
  )
  expect(approvalId).toMatch(/^[0-9a-f-]{36}$/)

  await page.getByRole('link', { name: '审批中心', exact: true }).click()
  await expect(page).toHaveURL(/\/approvals$/)
  const pendingRow = page.getByRole('row').filter({ hasText: '待审批' }).first()
  await pendingRow.getByRole('button', { name: /批\s*准/ }).click()
  const approveDialog = page.getByRole('dialog')
  await approveDialog.getByLabel('理由').fill('工作流审批通过')
  await approveDialog.getByRole('button', { name: '确认批准' }).click()
  await expect(page.getByText('暂无待办审批')).toBeVisible()

  // 批准后工作流继续执行到终态。
  await page.goto(`/runs/${runId}`)
  await expect(page.getByText('已完成', { exact: true })).toBeVisible()
  await expect(page.getByText(expectedOutput, { exact: true })).toBeVisible()

  expect(queryRuntimeDatabase(
    `SELECT status FROM approvals WHERE id='${approvalId}'`,
  )).toBe('approved')
  expect(queryRuntimeDatabase(
    `SELECT count(*) FROM run_commands WHERE run_id='${runId}' AND action='approve' AND processed_at IS NOT NULL`,
  )).toBe('1')
})
