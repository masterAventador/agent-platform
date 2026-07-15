import { expect, test } from '@playwright/test'

import { registerAndLogin } from './helpers/auth'


test('用户可以从已发布数字员工开始多轮会话并追加输入', async ({ page }) => {
  await registerAndLogin(page)

  await page.getByRole('link', { name: '数字员工' }).click()
  await page.getByRole('button', { name: '创建数字员工' }).click()
  await page.getByLabel('员工名称').fill('会话研究专员')
  await page.getByLabel('岗位说明').fill('负责围绕同一主题持续研究')
  await page.getByLabel('系统指令').fill('围绕用户多轮输入持续补充结论。')
  await expect(page.getByRole('checkbox', { name: '支持对话' })).toBeChecked()
  await page.getByRole('button', { name: '保存草稿' }).click()
  await page.getByRole('button', { name: '发布员工' }).click()
  await expect(page.getByText('已发布', { exact: true })).toBeVisible()

  await page.getByRole('button', { name: '开始会话' }).click()
  await expect(page).toHaveURL(/\/conversations\/[0-9a-f-]+$/)
  await expect(page.getByRole('heading', { name: '会话研究专员' })).toBeVisible()
  await expect(page.getByText('暂无消息')).toBeVisible()

  await page.getByLabel('追加消息').fill('请先列出三个竞品方向')
  await page.getByRole('button', { name: '发送' }).click()
  await expect(page.getByText('请先列出三个竞品方向')).toBeVisible()
  await expect(page.getByText('排队中', { exact: true })).toBeVisible()

  await page.getByLabel('追加消息').fill('继续补充每个方向的风险')
  await page.getByRole('button', { name: '发送' }).click()
  await expect(page.getByText('继续补充每个方向的风险')).toBeVisible()

  await page.getByRole('link', { name: '会话中心' }).click()
  await expect(page).toHaveURL(/\/conversations$/)
  await expect(page.getByText('会话研究专员')).toBeVisible()
})
