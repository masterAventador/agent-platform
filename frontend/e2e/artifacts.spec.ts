import { expect, test } from '@playwright/test'

import { registerAndLogin } from './helpers/auth'


const artifactId = '00000000-0000-4000-8000-000000000404'

test('用户可以上传任务附件并查看、下载、定位和删除产物', async ({ page }) => {
  await registerAndLogin(page)

  await page.getByRole('link', { name: '数字员工' }).click()
  await page.getByRole('button', { name: '创建数字员工' }).click()
  await page.getByLabel('员工名称').fill('附件处理专员')
  await page.getByLabel('岗位说明').fill('读取附件并生成任务产物')
  await page.getByRole('checkbox', { name: '支持文件上传' }).check()
  await page.getByLabel('系统指令').fill('读取任务附件，并将结果保存为产物。')
  await page.getByRole('button', { name: '保存草稿' }).click()
  await page.getByRole('button', { name: '发布员工' }).click()
  await expect(page.getByText('已发布', { exact: true })).toBeVisible()

  const currentUserResponse = await page.request.get('/api/v1/auth/me')
  expect(currentUserResponse.ok()).toBeTruthy()
  const currentUser = await currentUserResponse.json() as {
    workspaces: Array<{ id: string }>
  }
  const tenantId = currentUser.workspaces[0]?.id
  expect(tenantId).toBeTruthy()

  let artifactDeleted = false
  await page.route('**/api/v1/runs/*/artifacts', async (route) => {
    await route.fulfill({
      contentType: 'application/json',
      body: JSON.stringify(artifactDeleted ? [] : [{
        id: artifactId,
        run_id: route.request().url().split('/').at(-2),
        name: 'result.txt',
        media_type: 'text/plain',
        size_bytes: 16,
        sha256: '0'.repeat(64),
        created_at: '2026-07-15T00:00:00Z',
      }]),
    })
  })
  await page.route(`**/api/v1/artifacts/${artifactId}/content`, async (route) => {
    await route.fulfill({
      contentType: 'text/plain',
      body: 'artifact content',
      headers: { 'Content-Disposition': 'attachment; filename="result.txt"' },
    })
  })
  await page.route(`**/api/v1/artifacts/${artifactId}`, async (route) => {
    expect(route.request().method()).toBe('DELETE')
    artifactDeleted = true
    await route.fulfill({ status: 204 })
  })
  await page.route('**/api/v1/runs/*/events', async (route) => {
    const runId = route.request().url().split('/').at(-2)
    await route.fulfill({
      contentType: 'application/json',
      body: JSON.stringify([{
        event_id: '00000000-0000-4000-8000-000000000405',
        event_version: '1.0',
        tenant_id: tenantId,
        employee_id: '00000000-0000-4000-8000-000000000406',
        run_id: runId,
        sequence: 1,
        type: 'artifact.created',
        occurred_at: '2026-07-15T00:00:00Z',
        payload: { artifact_id: artifactId, name: 'result.txt' },
      }]),
    })
  })
  await page.route(/\/api\/v1\/runs\/[^/]+\/stream(?:\?.*)?$/, async (route) => {
    await route.fulfill({
      contentType: 'text/event-stream',
      body: ': controlled runtime boundary\n\n',
      headers: { 'Cache-Control': 'no-cache' },
    })
  })

  await page.getByRole('button', { name: '发起任务' }).click()
  const fileChooserPromise = page.waitForEvent('filechooser')
  await page.getByRole('button', { name: '选择文件' }).click()
  const fileChooser = await fileChooserPromise
  await fileChooser.setFiles({
    name: 'brief.txt',
    mimeType: 'text/plain',
    buffer: Buffer.from('brief'),
  })
  await expect(page.getByText('brief.txt')).toBeVisible()
  await page.getByLabel('任务内容').fill('读取附件并生成结果')

  const uploadResponsePromise = page.waitForResponse((response) => (
    response.request().method() === 'POST'
      && response.url().endsWith('/api/v1/files')
  ))
  await page.getByRole('button', { name: '确认发起' }).click()
  const uploadResponse = await uploadResponsePromise
  expect(uploadResponse.status()).toBe(201)

  await expect(page).toHaveURL(/\/runs\/[0-9a-f-]+$/)
  const runId = page.url().split('/').at(-1)
  expect(runId).toBeTruthy()
  const attachmentsResponse = await page.request.get(
    `/api/v1/runs/${runId}/attachments`,
    { headers: { 'X-Tenant-ID': tenantId ?? '' } },
  )
  expect(attachmentsResponse.ok()).toBeTruthy()
  await expect.poll(async () => {
    const attachments = await attachmentsResponse.json() as Array<{
      workspace_path: string
      file: { name: string }
    }>
    return attachments
  }).toEqual([expect.objectContaining({
    workspace_path: expect.stringMatching(/^inputs\/.+\/brief\.txt$/),
    file: expect.objectContaining({ name: 'brief.txt' }),
  })])

  await expect(page.getByRole('heading', { name: '任务详情' })).toBeVisible()
  await expect(page.getByText('生成任务产物')).toBeVisible()
  await expect(page.getByText('result.txt')).toHaveCount(2)

  await page.getByRole('button', { name: '预览 result.txt' }).click()
  await expect(page.getByText('artifact content')).toBeVisible()
  await page.getByRole('button', { name: /关\s*闭/ }).click()

  const downloadPromise = page.waitForEvent('download')
  await page.getByRole('button', { name: '下载 result.txt' }).click()
  const download = await downloadPromise
  expect(download.suggestedFilename()).toBe('result.txt')

  await page.getByRole('button', { name: '定位 result.txt' }).click()
  await expect(page.locator(`[data-artifact-id="${artifactId}"]`)).toBeInViewport()

  await page.getByRole('button', { name: '删除 result.txt' }).click()
  await expect(page.getByRole('button', { name: '预览 result.txt' })).toHaveCount(0)
  await expect(page.getByText('暂无任务产物')).toBeVisible()

  await page.getByRole('link', { name: '任务中心' }).click()
  await expect(page).toHaveURL(/\/runs$/)
})
