import { expect, test } from '@playwright/test'

import { registerAndLogin } from './helpers/auth'


test('用户可以在记忆中心完成新增、搜索、纠正、禁用与删除闭环', async ({ page }) => {
  await registerAndLogin(page)

  const memoryText = `记忆闭环-偏好中文签名-${Date.now()}`
  const correctedText = `记忆闭环-偏好英文签名-${Date.now()}`

  await page.getByRole('link', { name: '记忆中心' }).click()
  await expect(page.getByRole('heading', { name: '记忆中心' })).toBeVisible()
  await expect(page.getByText('暂无长期记忆')).toBeVisible()

  // 新增用户级记忆
  await page.getByRole('button', { name: '新增记忆' }).click()
  await page.getByLabel('记忆内容').fill(memoryText)
  await page.getByRole('button', { name: /保\s*存/ }).click()
  await expect(page.getByText(memoryText)).toBeVisible()
  await expect(page.getByText('生效中')).toBeVisible()

  // 关键词搜索
  await page.getByLabel('搜索记忆').fill('偏好中文签名')
  await page.getByLabel('搜索记忆').press('Enter')
  await expect(page.getByText(memoryText)).toBeVisible()
  await page.getByLabel('搜索记忆').fill('不存在的关键字')
  await page.getByLabel('搜索记忆').press('Enter')
  await expect(page.getByText('暂无长期记忆')).toBeVisible()
  await page.getByLabel('搜索记忆').fill('')
  await page.getByLabel('搜索记忆').press('Enter')

  // 纠正
  await page.getByRole('button', { name: /纠\s*正/ }).click()
  await page.getByLabel('记忆内容').fill(correctedText)
  await page.getByRole('button', { name: /保\s*存/ }).click()
  await expect(page.getByText(correctedText)).toBeVisible()
  await expect(page.getByText(memoryText)).toBeHidden()

  // 禁用与启用
  await page.getByRole('button', { name: /禁\s*用/ }).click()
  await expect(page.getByText('已禁用')).toBeVisible()
  await page.getByRole('button', { name: /启\s*用/ }).click()
  await expect(page.getByText('生效中')).toBeVisible()

  // 删除后不可恢复
  await page.getByRole('button', { name: /删\s*除/ }).click()
  await page.getByRole('button', { name: '确认删除' }).click()
  await expect(page.getByText('暂无长期记忆')).toBeVisible()
  await page.reload()
  await expect(page.getByRole('heading', { name: '记忆中心' })).toBeVisible()
  await expect(page.getByText('暂无长期记忆')).toBeVisible()
})

test('长期记忆按租户隔离：其他企业看不到本企业记忆', async ({ browser }) => {
  const tenantAContext = await browser.newContext()
  const tenantAPage = await tenantAContext.newPage()
  await registerAndLogin(tenantAPage)

  const secretMemory = `租户A机密偏好-${Date.now()}`
  await tenantAPage.getByRole('link', { name: '记忆中心' }).click()
  await tenantAPage.getByRole('button', { name: '新增记忆' }).click()
  await tenantAPage.getByLabel('记忆内容').fill(secretMemory)
  await tenantAPage.getByRole('button', { name: /保\s*存/ }).click()
  await expect(tenantAPage.getByText(secretMemory)).toBeVisible()

  const tenantBContext = await browser.newContext()
  const tenantBPage = await tenantBContext.newPage()
  await registerAndLogin(tenantBPage)
  await tenantBPage.getByRole('link', { name: '记忆中心' }).click()
  await expect(tenantBPage.getByRole('heading', { name: '记忆中心' })).toBeVisible()
  await expect(tenantBPage.getByText('暂无长期记忆')).toBeVisible()
  await expect(tenantBPage.getByText(secretMemory)).toBeHidden()

  await tenantAContext.close()
  await tenantBContext.close()
})
