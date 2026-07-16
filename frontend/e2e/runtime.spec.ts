import { execFileSync } from 'node:child_process'
import { existsSync } from 'node:fs'

import { expect, test } from '@playwright/test'

import { registerAndLogin } from './helpers/auth'
import { queryRuntimeDatabase } from './helpers/runtime-infra'


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

test('用户可以通过真实 Worker 提交动态输入并查看结构化输出', async ({ page }) => {
  await registerAndLogin(page)

  const inputSchema = {
    type: 'object',
    required: ['topic', 'priority', 'due_date', 'keywords'],
    additionalProperties: false,
    properties: {
      topic: { type: 'string', title: '主题', minLength: 2 },
      priority: { type: 'string', title: '优先级', enum: ['low', 'high'] },
      due_date: { type: 'string', title: '截止日期', format: 'date' },
      keywords: { type: 'array', title: '关键词', items: { type: 'string' } },
    },
  }
  const outputSchema = {
    type: 'object',
    'x-agent-platform-view': 'cards',
    required: ['cards'],
    properties: {
      cards: {
        type: 'array',
        items: {
          type: 'object',
          required: ['title', 'score'],
          properties: {
            title: { type: 'string' },
            score: { type: 'number' },
          },
        },
      },
      summary: { type: 'string' },
    },
  }

  await page.getByRole('link', { name: '数字员工' }).click()
  await page.getByRole('button', { name: '创建数字员工' }).click()
  await page.getByLabel('员工名称').fill('结构化验收专员')
  await page.getByLabel('岗位说明').fill('验证动态输入和结构化输出链路')
  await page.getByLabel('系统指令').fill('根据用户输入生成结构化线索卡片。')
  await page.getByLabel('输入 Schema JSON').fill(JSON.stringify(inputSchema, null, 2))
  await page.getByLabel('输出 Schema JSON').fill(JSON.stringify(outputSchema, null, 2))
  await page.getByRole('button', { name: '保存草稿' }).click()

  await expect(page).toHaveURL(/\/employees\/[0-9a-f-]+$/)
  await page.getByRole('button', { name: '发布员工' }).click()
  await expect(page.getByText('已发布', { exact: true })).toBeVisible()

  await page.getByRole('button', { name: '发起任务' }).click()
  await page.getByLabel('主题').fill('短视频投放')
  await page.getByLabel('优先级').selectOption('high')
  await page.getByLabel('截止日期').fill('2026-07-16')
  await page.getByLabel('关键词').fill('线索\n转化')
  await page.getByRole('button', { name: '确认发起' }).click()

  await expect(page).toHaveURL(/\/runs\/[0-9a-f-]+$/)
  await expect(page.getByText('已完成', { exact: true })).toBeVisible()
  await expect(page.getByText('结构化结果', { exact: true })).toBeVisible()
  await expect(page.getByText('线索 A', { exact: true })).toBeVisible()
  await expect(page.getByText('score', { exact: true })).toBeVisible()
  await expect(page.getByText('0.91', { exact: true })).toBeVisible()
  await expect(page.getByText('已生成结构化卡片', { exact: true })).toBeVisible()

  const runId = new URL(page.url()).pathname.split('/').at(-1)
  expect(runId).toMatch(/^[0-9a-f-]{36}$/)
  const savedInput = queryRuntimeDatabase(
    `SELECT concat_ws('|', (input_data::jsonb)->>'topic', (input_data::jsonb)->>'priority', (input_data::jsonb)->>'due_date', (input_data::jsonb)#>>'{keywords,0}') FROM runs WHERE id = '${runId}'`,
  )
  expect(savedInput).toBe('短视频投放|high|2026-07-16|线索')
  expect(queryRuntimeDatabase(
    `SELECT payload #>> '{content,cards,0,title}' FROM run_events WHERE run_id = '${runId}' AND event_type = 'message.output'`,
  )).toBe('线索 A')
  expect(queryRuntimeDatabase(
    `SELECT payload #>> '{output,cards,0,title}' FROM run_events WHERE run_id = '${runId}' AND event_type = 'run.completed'`,
  )).toBe('线索 A')

  const downloadPromise = page.waitForEvent('download')
  await page.getByRole('button', { name: '导出 JSON' }).click()
  const download = await downloadPromise
  expect(download.suggestedFilename()).toBe(`${runId}-output.json`)
})

test('用户可以取消正在调用模型的真实 Worker 任务且运行停止', async ({ page }) => {
  await registerAndLogin(page)

  // slow-cancel 是 Worker 夹具专用 alias，不通过只开放平台模型的生产 UI 暴露。
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
        model: { kind: 'gateway_alias', alias: 'slow-cancel' },
      }),
    })
  })

  await page.getByRole('link', { name: '数字员工' }).click()
  await page.getByRole('button', { name: '创建数字员工' }).click()
  await page.getByLabel('员工名称').fill('取消验收专员')
  await page.getByLabel('岗位说明').fill('验证真实 Worker 的取消链路')
  await page.getByLabel('系统指令').fill('等待模型回复，除非用户取消任务。')
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
