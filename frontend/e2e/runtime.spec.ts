import { execFileSync } from 'node:child_process'

import { expect, test } from '@playwright/test'

import { registerAndLogin } from './helpers/auth'
import { queryRuntimeDatabase } from './helpers/runtime-infra'


const modelProvider = process.env.PLAYWRIGHT_RUNTIME_MODEL_PROVIDER ?? 'openai'
const modelName = process.env.PLAYWRIGHT_RUNTIME_MODEL_NAME ?? 'gpt-5'
const expectedOutput = process.env.PLAYWRIGHT_RUNTIME_EXPECTED_OUTPUT
  ?? 'Runtime E2E completed in the real worker.'

test('用户可以通过真实 Worker 完成自主员工任务并看到模型输出', async ({ page }) => {
  await registerAndLogin(page)

  await page.getByRole('link', { name: '数字员工' }).click()
  await page.getByRole('button', { name: '创建数字员工' }).click()
  await page.getByLabel('员工名称').fill('运行时验收专员')
  await page.getByLabel('岗位说明').fill('验证数字员工真实运行链路')
  await page.getByLabel('系统指令').fill(`无论用户输入什么，只回复：${expectedOutput}`)
  await page.getByLabel('模型供应商').fill(modelProvider)
  await page.getByLabel('模型名称').fill(modelName)
  await page.getByRole('button', { name: '保存草稿' }).click()

  await expect(page).toHaveURL(/\/employees\/[0-9a-f-]+$/)
  await page.getByRole('button', { name: '发布员工' }).click()
  await expect(page.getByText('已发布', { exact: true })).toBeVisible()

  await page.getByRole('button', { name: '发起任务' }).click()
  await page.getByLabel('任务内容').fill('执行真实运行时端到端验证')
  await page.getByRole('button', { name: '确认发起' }).click()

  await expect(page).toHaveURL(/\/runs\/[0-9a-f-]+$/)
  await expect(page.getByText('已完成', { exact: true })).toBeVisible()
  await expect(page.getByText('任务开始执行', { exact: true })).toBeVisible()
  await expect(page.getByText('任务执行完成', { exact: true })).toBeVisible()
  await expect(page.getByText('模型输出', { exact: true })).toBeVisible()
  await expect(page.getByText(expectedOutput, { exact: true })).toBeVisible()

  const runId = new URL(page.url()).pathname.split('/').at(-1)
  expect(runId).toMatch(/^[0-9a-f-]{36}$/)
  const commandState = queryRuntimeDatabase(
    `SELECT (dispatched_at IS NOT NULL)::text || '|' || (processed_at IS NOT NULL)::text FROM run_commands WHERE run_id = '${runId}' AND action = 'start'`,
  )
  expect(commandState).toBe('true|true')

  const leaseState = queryRuntimeDatabase(
    `SELECT id::text || '|' || status FROM sandbox_leases WHERE run_id = '${runId}'`,
  )
  const [leaseId, leaseStatus] = leaseState.split('|')
  expect(leaseId).toMatch(/^[0-9a-f-]{36}$/)
  expect(leaseStatus).toBe('deleted')
  const remainingSandboxIds = execFileSync(
    'docker',
    ['ps', '-aq', '--filter', `label=agent-platform.sandbox.lease-id=${leaseId}`],
    { encoding: 'utf8' },
  ).trim()
  expect(remainingSandboxIds).toBe('')
})
