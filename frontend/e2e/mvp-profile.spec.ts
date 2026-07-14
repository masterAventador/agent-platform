import { writeFileSync } from 'node:fs'

import { expect, test } from '@playwright/test'

import { registerAndLogin } from './helpers/auth'

const expectedOutput = 'local stub completion'
const resultFile = process.env.PLAYWRIGHT_MVP_RESULT_FILE

test('用户通过 MVP 完整栈完成数字员工任务并在刷新后看到持久化终态', async ({ page }) => {
  if (!resultFile) throw new Error('PLAYWRIGHT_MVP_RESULT_FILE is required')

  await registerAndLogin(page)

  await page.getByRole('link', { name: '数字员工' }).click()
  await page.getByRole('button', { name: '创建数字员工' }).click()
  await page.getByLabel('员工名称').fill('MVP 全链路验收专员')
  await page.getByLabel('岗位说明').fill('验证生产 Worker 经过 LiteLLM Stub 的完整业务链路')
  await page.getByLabel('系统指令').fill('完成任务后直接给出简短结果。')
  await page.getByRole('button', { name: '保存草稿' }).click()

  await expect(page).toHaveURL(/\/employees\/[0-9a-f-]+$/)
  await page.getByRole('button', { name: '发布员工' }).click()
  await expect(page.getByText('已发布', { exact: true })).toBeVisible()

  await page.getByRole('button', { name: '发起任务' }).click()
  await page.getByLabel('任务内容').fill('mvp-web-flow')
  await page.getByRole('button', { name: '确认发起' }).click()

  await expect(page).toHaveURL(/\/runs\/[0-9a-f-]+$/)
  await expect(page.getByText('已完成', { exact: true })).toBeVisible()
  await expect(page.getByText('任务开始执行', { exact: true })).toBeVisible()
  await expect(page.getByText('任务执行完成', { exact: true })).toBeVisible()
  await expect(page.getByText('模型输出', { exact: true })).toBeVisible()
  await expect(page.getByText(expectedOutput, { exact: true })).toBeVisible()

  const runId = new URL(page.url()).pathname.split('/').at(-1)
  expect(runId).toMatch(
    /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/,
  )
  writeFileSync(resultFile, `${runId}\n`, { encoding: 'utf8', mode: 0o600 })

  await page.reload()
  await expect(page.getByText('已完成', { exact: true })).toBeVisible()
  await expect(page.getByText(expectedOutput, { exact: true })).toBeVisible()
})
