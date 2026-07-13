import { expect, test } from '@playwright/test'

import { registerAndLogin } from './helpers/auth'
import {
  malformedPayloadMarker,
  prepareDeadLetterFixture,
  rawFieldValueMarker,
  setDeadLetterWorkspaceRole,
  validPayloadMarker,
} from './helpers/dead-letters'


test('owner 可以安全查看并重放已结算死信，malformed 记录不可重放', async ({ page }) => {
  const email = await registerAndLogin(page)
  const fixture = prepareDeadLetterFixture(email)

  await page.getByRole('link', { name: '任务运维' }).click()
  await expect(page).toHaveURL(/\/operations\/dead-letters$/)
  await expect(page.getByRole('heading', { name: '死信管理' })).toBeVisible()

  const validRow = page.getByTestId(`dead-letter-row-${fixture.valid_dead_letter_id}`)
  const malformedRow = page.getByTestId(
    `dead-letter-row-${fixture.malformed_dead_letter_id}`,
  )
  await expect(validRow).toBeVisible()
  await expect(validRow).toContainText('已结算')
  await expect(validRow.getByRole('button', { name: '重放任务' })).toBeEnabled()
  await expect(malformedRow).toBeVisible()
  await expect(malformedRow.getByRole('button', { name: '重放任务' })).toBeDisabled()

  const pageText = page.locator('body')
  await expect(pageText).not.toContainText(validPayloadMarker)
  await expect(pageText).not.toContainText(malformedPayloadMarker)
  await expect(pageText).not.toContainText(rawFieldValueMarker)

  await validRow.getByRole('button', { name: '重放任务' }).click()
  const confirmation = page.getByRole('dialog', { name: '确认重放任务' })
  await expect(confirmation).toBeVisible()
  await Promise.all([
    page.waitForResponse((response) =>
      response.url().includes(`/api/v1/run-dead-letters/${fixture.valid_dead_letter_id}/replay`)
      && response.request().method() === 'POST'
      && response.status() === 200),
    confirmation.getByRole('button', { name: '确认重放' }).click(),
  ])

  const replayedLink = validRow.getByRole('link', { name: '查看新任务' })
  await expect(replayedLink).toBeVisible()
  const replayedHref = await replayedLink.getAttribute('href')
  expect(replayedHref).toMatch(/^\/runs\/[0-9a-f-]+$/)

  await page.reload()
  const refreshedValidRow = page.getByTestId(
    `dead-letter-row-${fixture.valid_dead_letter_id}`,
  )
  await expect(refreshedValidRow.getByRole('link', { name: '查看新任务' })).toHaveAttribute(
    'href',
    replayedHref!,
  )
  await expect(refreshedValidRow.getByRole('button', { name: '重放任务' })).toBeDisabled()
  await expect(page.getByTestId(
    `dead-letter-row-${fixture.malformed_dead_letter_id}`,
  ).getByRole('button', { name: '重放任务' })).toBeDisabled()

  await refreshedValidRow.getByRole('link', { name: '查看原任务' }).click()
  await expect(page).toHaveURL(`/runs/${fixture.original_run_id}`)
  await expect(page.getByRole('heading', { name: '任务详情' })).toBeVisible()
  await expect(page.getByText('失败', { exact: true })).toBeVisible()
  await expect(page.getByText('E2E 原任务保持不变')).toBeVisible()

  await page.goto('/operations/dead-letters')
  await page.getByTestId(`dead-letter-row-${fixture.valid_dead_letter_id}`)
    .getByRole('link', { name: '查看新任务' })
    .click()
  await expect(page).toHaveURL(replayedHref!)
  await expect(page.getByRole('heading', { name: '任务详情' })).toBeVisible()
  await expect(page.getByText('排队中', { exact: true })).toBeVisible()
  await expect(page.getByText('E2E 原任务保持不变')).toBeVisible()
})

test('admin 可以看到入口并直接访问死信管理', async ({ page }) => {
  const email = await registerAndLogin(page)
  const fixture = prepareDeadLetterFixture(email)
  setDeadLetterWorkspaceRole(email, 'admin')
  await page.reload()

  await expect(page.getByRole('link', { name: '任务运维' })).toBeVisible()
  await page.goto('/operations/dead-letters')
  await expect(page.getByRole('heading', { name: '死信管理' })).toBeVisible()
  await expect(page.getByTestId(`dead-letter-row-${fixture.valid_dead_letter_id}`)).toBeVisible()
})

test('member 看不到入口且直接访问时受控拒绝', async ({ page }) => {
  const email = await registerAndLogin(page)
  setDeadLetterWorkspaceRole(email, 'member')
  await page.reload()

  await expect(page.getByRole('link', { name: '任务运维' })).toHaveCount(0)
  await page.goto('/operations/dead-letters')
  await expect(page.getByText('无权访问死信管理')).toBeVisible()
  await expect(page.getByText(
    '当前工作区没有执行此操作的权限，请联系工作区所有者。',
  )).toBeVisible()
})
