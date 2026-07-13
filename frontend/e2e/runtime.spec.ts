import { execFileSync } from 'node:child_process'
import { existsSync } from 'node:fs'

import { expect, test } from '@playwright/test'

import { registerAndLogin } from './helpers/auth'
import { queryRuntimeDatabase } from './helpers/runtime-infra'


const modelProvider = process.env.PLAYWRIGHT_RUNTIME_MODEL_PROVIDER ?? 'openai'
const modelName = process.env.PLAYWRIGHT_RUNTIME_MODEL_NAME ?? 'gpt-5'
const expectedOutput = process.env.PLAYWRIGHT_RUNTIME_EXPECTED_OUTPUT
  ?? 'Runtime E2E completed in the real worker.'
const slowModelStartedFile = '/tmp/agent-platform-runtime-e2e-slow-model-started'
const slowModelStoppedFile = '/tmp/agent-platform-runtime-e2e-slow-model-stopped'
const slowModelSideEffectFile = '/tmp/agent-platform-runtime-e2e-slow-model-side-effect'

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

test('用户可以取消正在调用模型的真实 Worker 任务且运行停止', async ({ page }) => {
  await registerAndLogin(page)

  await page.getByRole('link', { name: '数字员工' }).click()
  await page.getByRole('button', { name: '创建数字员工' }).click()
  await page.getByLabel('员工名称').fill('取消验收专员')
  await page.getByLabel('岗位说明').fill('验证真实 Worker 的取消链路')
  await page.getByLabel('系统指令').fill('等待模型回复，除非用户取消任务。')
  await page.getByLabel('模型供应商').fill('openai')
  await page.getByLabel('模型名称').fill('slow-cancel')
  await page.getByRole('button', { name: '保存草稿' }).click()
  await page.getByRole('button', { name: '发布员工' }).click()
  await expect(page.getByText('已发布', { exact: true })).toBeVisible()

  await page.getByRole('button', { name: '发起任务' }).click()
  await page.getByLabel('任务内容').fill('启动后保持运行，等待我取消')
  await page.getByRole('button', { name: '确认发起' }).click()

  await expect(page).toHaveURL(/\/runs\/[0-9a-f-]+$/)
  await expect.poll(() => existsSync(slowModelStartedFile)).toBe(true)

  await page.getByRole('button', { name: '取消任务' }).click()
  await expect(page.getByRole('button', { name: '取消处理中' })).toBeDisabled()
  await expect(page.getByText('已取消', { exact: true })).toBeVisible()
  await expect(page.getByText('任务开始执行', { exact: true })).toBeVisible()
  await expect(page.getByText('任务已取消', { exact: true })).toBeVisible()
  await expect.poll(() => existsSync(slowModelStoppedFile)).toBe(true)

  const runId = new URL(page.url()).pathname.split('/').at(-1)
  expect(runId).toMatch(/^[0-9a-f-]{36}$/)
  const commandStates = queryRuntimeDatabase(
    `SELECT action || '|' || (processed_at IS NOT NULL)::text FROM run_commands WHERE run_id = '${runId}' ORDER BY created_at`,
  )
  expect(commandStates.split('\n')).toEqual([
    'start|true',
    'cancel|true',
  ])
  expect(queryRuntimeDatabase(
    `SELECT status FROM runs WHERE id = '${runId}'`,
  )).toBe('cancelled')
  expect(queryRuntimeDatabase(
    `SELECT count(*) FILTER (WHERE event_type = 'run.cancelled')::text || '|' || count(*) FILTER (WHERE event_type = 'run.completed')::text || '|' || count(*) FILTER (WHERE event_type = 'message.output')::text FROM run_events WHERE run_id = '${runId}'`,
  )).toBe('1|0|0')
  expect(existsSync(slowModelSideEffectFile)).toBe(false)
})
