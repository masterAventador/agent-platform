import { writeFileSync } from 'node:fs'

import { expect, test, type Page } from '@playwright/test'

import { loginWithDemoAccount } from './helpers/auth'

const expectedOutput = 'local stub completion'
const resultFile = process.env.PLAYWRIGHT_MVP_RESULT_FILE
const failureResultFile = process.env.PLAYWRIGHT_MVP_FAILURE_RESULT_FILE
const artifactResultFile = process.env.PLAYWRIGHT_MVP_ARTIFACT_RESULT_FILE

type WorkbenchSummary = {
  employees: { total: number; published: number }
  runs: { total: number; completed: number; failed: number }
}

async function getWorkbenchSummary(page: Page): Promise<WorkbenchSummary> {
  const response = await page.request.get('/api/v1/workbench/summary')
  expect(response.ok()).toBeTruthy()
  return await response.json() as WorkbenchSummary
}

async function openOrCreatePublishedEmployee(
  page: Page,
  options: {
    name: string
    roleDescription: string
    systemPrompt: string
    fileUpload?: boolean
  },
): Promise<void> {
  await page.getByRole('link', { name: '数字员工' }).click()
  const existingEmployee = page.getByText(options.name, { exact: true }).first()
  if (await existingEmployee.count()) {
    await existingEmployee.click()
    await expect(page).toHaveURL(/\/employees\/[0-9a-f-]+$/)
    return
  }

  await page.getByRole('button', { name: '创建数字员工' }).click()
  await page.getByLabel('员工名称').fill(options.name)
  await page.getByLabel('岗位说明').fill(options.roleDescription)
  await page.getByLabel('系统指令').fill(options.systemPrompt)
  if (options.fileUpload) {
    await page.getByRole('checkbox', { name: '支持文件上传' }).check()
  }
  await page.getByRole('button', { name: '保存草稿' }).click()
  await expect(page).toHaveURL(/\/employees\/[0-9a-f-]+$/)
  await page.getByRole('button', { name: '发布员工' }).click()
  await expect(page.getByText('已发布', { exact: true })).toBeVisible()
}

test('用户通过 MVP 完整栈完成数字员工任务并在刷新后看到持久化终态', async ({ page }) => {
  if (!resultFile) throw new Error('PLAYWRIGHT_MVP_RESULT_FILE is required')

  await loginWithDemoAccount(page)
  const before = await getWorkbenchSummary(page)
  await openOrCreatePublishedEmployee(page, {
    name: 'MVP 全链路验收专员',
    roleDescription: '验证生产 Worker 经过 LiteLLM Stub 的完整业务链路',
    systemPrompt: '完成任务后直接给出简短结果。',
  })

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

  await page.getByRole('link', { name: '工作台' }).click()
  const after = await getWorkbenchSummary(page)
  expect(after.employees.total).toBeGreaterThanOrEqual(before.employees.total)
  expect(after.employees.published).toBeGreaterThanOrEqual(before.employees.published)
  expect(after.runs.total).toBe(before.runs.total + 1)
  expect(after.runs.completed).toBe(before.runs.completed + 1)
  expect(after.runs.failed).toBe(before.runs.failed)
  await expect(page.getByLabel('数字员工总数')).toContainText(`${after.employees.total}`)
  await expect(page.getByLabel('已发布员工')).toContainText(`${after.employees.published}`)
  await expect(page.getByLabel('任务总数', { exact: true })).toContainText(`${after.runs.total}`)
  await expect(page.getByLabel('已完成任务')).toContainText(`${after.runs.completed}`)
  await expect(page.getByLabel('失败任务总数')).toContainText(`${after.runs.failed}`)
  await expect(page.getByText('后端服务正常')).toBeVisible()
})

test('受控真实 Worker 失败会持久化并在工作台显示失败任务', async ({ page }) => {
  if (!failureResultFile) throw new Error('PLAYWRIGHT_MVP_FAILURE_RESULT_FILE is required')

  await loginWithDemoAccount(page)
  const before = await getWorkbenchSummary(page)
  await openOrCreatePublishedEmployee(page, {
    name: 'MVP 失败状态验收专员',
    roleDescription: '验证生产 Worker 的可信失败状态与工作台统计',
    systemPrompt: '按用户请求执行，并保留失败状态。',
  })

  await page.getByRole('button', { name: '发起任务' }).click()
  await page.getByLabel('任务内容').fill('mvp-web-flow-failure')
  await page.getByRole('button', { name: '确认发起' }).click()

  await expect(page).toHaveURL(/\/runs\/[0-9a-f-]+$/)
  await expect(page.getByText('失败', { exact: true })).toBeVisible()
  await expect(page.getByText('任务执行失败', { exact: true })).toBeVisible()

  const runId = new URL(page.url()).pathname.split('/').at(-1)
  expect(runId).toMatch(
    /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/,
  )
  writeFileSync(failureResultFile, `${runId}\n`, { encoding: 'utf8', mode: 0o600 })

  await page.getByRole('link', { name: '工作台' }).click()
  const after = await getWorkbenchSummary(page)
  expect(after.employees.total).toBeGreaterThanOrEqual(before.employees.total)
  expect(after.runs.total).toBe(before.runs.total + 1)
  expect(after.runs.failed).toBe(before.runs.failed + 1)
  await expect(page.getByLabel('数字员工总数')).toContainText(`${after.employees.total}`)
  await expect(page.getByLabel('任务总数', { exact: true })).toContainText(`${after.runs.total}`)
  await expect(page.getByLabel('失败任务总数')).toContainText(`${after.runs.failed}`)
  await expect(page.getByLabel('失败任务', { exact: true })).toContainText(`${after.runs.failed}`)
  await expect(page.getByText('有任务需要关注')).toBeVisible()
  await expect(page.getByText('后端服务正常')).toBeVisible()
})

test('用户上传附件后真实 Agent 读取内容并发布派生产物完成全栈闭环', async ({ page }) => {
  if (!artifactResultFile) throw new Error('PLAYWRIGHT_MVP_ARTIFACT_RESULT_FILE is required')

  await loginWithDemoAccount(page)
  await openOrCreatePublishedEmployee(page, {
    name: 'MVP 产物附件闭环验收专员',
    roleDescription: '读取用户附件内容，在真实沙箱生成并发布任务产物',
    systemPrompt: '读取任务附件，根据附件内容生成文件，并使用 create_artifact 发布。',
    fileUpload: true,
  })

  await page.getByRole('button', { name: '发起任务' }).click()
  const fileChooserPromise = page.waitForEvent('filechooser')
  await page.getByRole('button', { name: '选择文件' }).click()
  const fileChooser = await fileChooserPromise
  await fileChooser.setFiles({
    name: 'brief.txt',
    mimeType: 'text/plain',
    buffer: Buffer.from('C04 attachment content: create the derived artifact'),
  })
  await expect(page.getByText('brief.txt')).toBeVisible()
  await page.getByLabel('任务内容').fill('mvp-artifact-flow')
  const uploadResponsePromise = page.waitForResponse((response) => (
    response.request().method() === 'POST' && response.url().endsWith('/api/v1/files')
  ))
  await page.getByRole('button', { name: '确认发起' }).click()
  expect((await uploadResponsePromise).status()).toBe(201)

  await expect(page).toHaveURL(/\/runs\/[0-9a-f-]+$/)
  await expect(page.getByText('已完成', { exact: true })).toBeVisible()
  await expect(page.getByText('result.txt').first()).toBeVisible()
  await expect(page.getByText('生成任务产物')).toBeVisible()

  const runId = new URL(page.url()).pathname.split('/').at(-1)
  expect(runId).toMatch(
    /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/,
  )
  const attachmentsResponse = await page.request.get(`/api/v1/runs/${runId}/attachments`)
  expect(attachmentsResponse.ok()).toBeTruthy()
  const attachments = await attachmentsResponse.json() as Array<{
    workspace_path: string
    file: { name: string }
  }>
  expect(attachments).toEqual([
    expect.objectContaining({
      workspace_path: expect.stringMatching(/^inputs\/.+\/brief\.txt$/),
      file: expect.objectContaining({ name: 'brief.txt' }),
    }),
  ])
  const artifactLocator = page.locator('[data-artifact-id]').first()
  const artifactId = await artifactLocator.getAttribute('data-artifact-id')
  expect(artifactId).toMatch(
    /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/,
  )
  writeFileSync(artifactResultFile, `${runId}|${artifactId}\n`, {
    encoding: 'utf8', mode: 0o600,
  })

  await page.getByRole('button', { name: '预览 result.txt' }).click()
  await expect(page.getByText(/C04 attachment content: create the derived artifact/)).toBeVisible()
  await page.getByRole('button', { name: /关\s*闭/ }).click()

  const downloadPromise = page.waitForEvent('download')
  await page.getByRole('button', { name: '下载 result.txt' }).click()
  expect((await downloadPromise).suggestedFilename()).toBe('result.txt')

  await page.getByRole('button', { name: '定位 result.txt' }).click()
  await expect(artifactLocator).toBeInViewport()
  await page.reload()
  await expect(page.getByText('result.txt').first()).toBeVisible()

  await page.getByRole('button', { name: '删除 result.txt' }).click()
  await expect(page.getByText('暂无任务产物')).toBeVisible()
})
