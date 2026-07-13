import { expect, test } from '@playwright/test'

import { registerAndLogin } from './helpers/auth'


test('用户可以从已发布数字员工发起任务并提交取消意图', async ({ page }) => {
  await registerAndLogin(page)
  await page.getByRole('link', { name: '数字员工' }).click()
  await page.getByRole('button', { name: '创建数字员工' }).click()
  await page.getByLabel('员工名称').fill('任务执行专员')
  await page.getByLabel('岗位说明').fill('执行用户提交的任务')
  await page.getByLabel('系统指令').fill('严格执行用户任务并返回结果。')
  await page.getByRole('button', { name: '保存草稿' }).click()
  await page.getByRole('button', { name: '发布员工' }).click()
  await expect(page.getByText('已发布', { exact: true })).toBeVisible()

  await page.getByRole('button', { name: '发起任务' }).click()
  await page.getByLabel('任务内容').fill('整理本周项目进展')
  await page.getByRole('button', { name: '确认发起' }).click()

  await expect(page).toHaveURL(/\/runs\/[0-9a-f-]+$/)
  await expect(page.getByRole('heading', { name: '任务详情' })).toBeVisible()
  await expect(page.getByText('排队中', { exact: true })).toBeVisible()
  await expect(page.getByText('整理本周项目进展')).toBeVisible()

  await page.getByRole('button', { name: '取消任务' }).click()
  await expect(page.getByRole('button', { name: '取消处理中' })).toBeDisabled()
  await expect(page.getByText('排队中', { exact: true })).toBeVisible()
  await expect(page.getByText('已取消', { exact: true })).toHaveCount(0)
  await expect(page.getByText('请求取消任务')).toBeVisible()

  await page.getByRole('link', { name: '任务中心' }).click()
  await expect(page).toHaveURL(/\/runs$/)
  await expect(page.getByText('排队中', { exact: true })).toBeVisible()
})
