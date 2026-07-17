import { expect, test } from '@playwright/test'

import { registerAndLogin } from './helpers/auth'


const AGENT_ONLY_GRAPH = JSON.stringify(
  {
    entrypoint: 'answer',
    nodes: [{ name: 'answer', type: 'agent', config: { prompt: '答复用户' }, next: null }],
  },
  null,
  2,
)

test('用户可以注册、发布工作流并创建引用它的流程数字员工', async ({ page }) => {
  await registerAndLogin(page)

  const workflowName = `E2E 工作流 ${Date.now()}`

  // 进入工作流中心并注册一个工作流。
  await page.getByRole('link', { name: '工作流中心' }).click()
  await expect(page).toHaveURL(/\/workflows$/)
  await page.getByLabel('工作流名称').fill(workflowName)
  await page.getByLabel('描述').fill('端到端验收工作流')
  const graphField = page.getByLabel('工作流图（JSON）')
  await graphField.fill(AGENT_ONLY_GRAPH)
  await page.getByRole('button', { name: '注册工作流' }).click()

  const workflowCard = page.locator('.ant-card', { hasText: workflowName })
  await expect(workflowCard).toBeVisible()
  await expect(workflowCard.getByText('草稿')).toBeVisible()

  // 发布工作流 v1。
  await workflowCard.getByRole('button', { name: '查看版本' }).click()
  await workflowCard.getByRole('button', { name: '发布 v1' }).click()
  await expect(workflowCard.getByText('已发布 v1')).toBeVisible()

  // 创建引用该工作流的流程数字员工。
  await page.getByRole('link', { name: '数字员工' }).click()
  await page.getByRole('button', { name: '创建数字员工' }).click()
  const employeeName = `流程员工 ${Date.now()}`
  await page.getByLabel('员工名称').fill(employeeName)
  await page.getByLabel('岗位说明').fill('执行固定工作流')
  await page.getByLabel('工作模式').click()
  await page.getByRole('option', { name: '固定流程' }).click()
  await page.getByLabel('引用工作流').click()
  await page.getByRole('option', { name: new RegExp(`${workflowName}（v1）`) }).click()
  await page.getByLabel('系统指令').fill('按已发布工作流执行')
  await page.getByRole('button', { name: '保存草稿' }).click()

  await expect(page).toHaveURL(/\/employees\/[0-9a-f-]+$/)
  await page.getByRole('button', { name: '发布员工' }).click()
  await expect(page.getByText('已发布', { exact: true })).toBeVisible()
})
