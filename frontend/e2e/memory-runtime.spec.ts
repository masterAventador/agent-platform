import { expect, test } from '@playwright/test'

import { registerAndLogin } from './helpers/auth'


const rememberedMemory = '记忆验收偏好-邮件使用中文签名-E2E'
const correctedMemory = '记忆验收偏好-邮件使用英文签名-E2E'

async function launchTask(page: import('@playwright/test').Page, content: string) {
  await page.getByRole('button', { name: '发起任务' }).click()
  await page.getByLabel('任务内容').fill(content)
  await page.getByRole('button', { name: '确认发起' }).click()
  await expect(page).toHaveURL(/\/runs\/[0-9a-f-]+$/)
  await expect(page.getByText('已完成', { exact: true })).toBeVisible()
}

test('长期记忆多轮闭环：第一轮任务产生记忆，后续任务真实召回、纠正生效、删除后不可召回', async ({ page }) => {
  await registerAndLogin(page)

  // 创建并发布开启长期记忆能力的员工
  await page.getByRole('link', { name: '数字员工' }).click()
  await page.getByRole('button', { name: '创建数字员工' }).click()
  await page.getByLabel('员工名称').fill('记忆验收专员')
  await page.getByLabel('岗位说明').fill('验证长期记忆提取与召回链路')
  await page.getByLabel('系统指令').fill('按记忆场景固定行为执行。')
  await page.getByRole('checkbox', { name: '启用长期记忆' }).check()
  await page.getByRole('button', { name: '保存草稿' }).click()
  await expect(page).toHaveURL(/\/employees\/[0-9a-f-]+$/)
  const employeeUrl = page.url()
  await page.getByRole('button', { name: '发布员工' }).click()
  await expect(page.getByText('已发布', { exact: true })).toBeVisible()

  // 第一轮任务：模型显式声明记忆，Worker 终态受控提取
  await launchTask(page, 'memory-write-e2e 请记录我的邮件偏好')
  await expect(page.getByText(new RegExp(rememberedMemory))).toBeVisible()

  // 记忆中心可见提取结果（真实持久化终态）
  await page.getByRole('link', { name: '记忆中心' }).click()
  await expect(page.getByText(rememberedMemory)).toBeVisible()
  await expect(page.getByText('任务提取')).toBeVisible()

  // 第二轮任务：运行时按权限注入记忆，模型输出证明真实召回
  await page.goto(employeeUrl)
  await launchTask(page, 'memory-recall-e2e 请结合我的偏好执行')
  await expect(page.getByText(new RegExp(`已召回记忆：.*${rememberedMemory}`))).toBeVisible()

  // 纠正记忆后再次运行：召回内容随纠正更新
  await page.getByRole('link', { name: '记忆中心' }).click()
  await expect(page.getByText(rememberedMemory)).toBeVisible()
  await page.getByRole('button', { name: /纠\s*正/ }).click()
  await page.getByLabel('记忆内容').fill(correctedMemory)
  await page.getByRole('button', { name: /保\s*存/ }).click()
  await expect(page.getByText(correctedMemory)).toBeVisible()

  await page.goto(employeeUrl)
  await launchTask(page, 'memory-recall-e2e 请再次结合我的偏好执行')
  await expect(page.getByText(new RegExp(`已召回记忆：.*${correctedMemory}`))).toBeVisible()

  // 删除记忆后再次运行：不可恢复召回
  await page.getByRole('link', { name: '记忆中心' }).click()
  await page.getByRole('button', { name: /删\s*除/ }).click()
  await page.getByRole('button', { name: '确认删除' }).click()
  await expect(page.getByText('暂无长期记忆')).toBeVisible()

  await page.goto(employeeUrl)
  await launchTask(page, 'memory-recall-e2e 删除后的召回验证')
  await expect(page.getByText('没有可用记忆', { exact: true })).toBeVisible()
})

test('未开启记忆能力的员工不提取记忆（禁用后不读不写）', async ({ page }) => {
  await registerAndLogin(page)

  await page.getByRole('link', { name: '数字员工' }).click()
  await page.getByRole('button', { name: '创建数字员工' }).click()
  await page.getByLabel('员工名称').fill('无记忆能力专员')
  await page.getByLabel('岗位说明').fill('验证记忆能力禁用行为')
  await page.getByLabel('系统指令').fill('按记忆场景固定行为执行。')
  await expect(page.getByRole('checkbox', { name: '启用长期记忆' })).not.toBeChecked()
  await page.getByRole('button', { name: '保存草稿' }).click()
  await page.getByRole('button', { name: '发布员工' }).click()
  await expect(page.getByText('已发布', { exact: true })).toBeVisible()

  await launchTask(page, 'memory-write-e2e 请记录我的邮件偏好')

  await page.getByRole('link', { name: '记忆中心' }).click()
  await expect(page.getByRole('heading', { name: '记忆中心' })).toBeVisible()
  await expect(page.getByText('暂无长期记忆')).toBeVisible()
})
