import { expect, test } from '@playwright/test'

import { registerAndLogin } from './helpers/auth'


test('用户可以创建知识库、上传解析文档并验证带引用的检索', async ({ page }) => {
  await registerAndLogin(page)
  await page.getByRole('link', { name: '知识库' }).click()
  await expect(page.getByText('还没有知识库')).toBeVisible()

  await page.getByRole('button', { name: '创建知识库' }).click()
  await page.getByLabel('知识库名称').fill('员工制度')
  await page.getByLabel('说明').fill('企业人事制度与员工手册')
  await page.getByRole('dialog').getByRole('button', { name: /创\s*建/ }).click()

  await expect(page).toHaveURL(/\/knowledge-bases\/[0-9a-f-]+$/)
  await expect(page.getByRole('heading', { name: '员工制度' })).toBeVisible()
  await page.getByLabel('选择文档').setInputFiles({
    name: '员工手册.txt',
    mimeType: 'text/plain',
    buffer: Buffer.from('员工每年享有十天年假。'),
  })
  await page.getByRole('button', { name: '上传并解析' }).click()
  await expect(page.getByText('员工手册.txt')).toBeVisible()
  await expect(page.getByText('解析完成')).toBeVisible()

  await page.getByLabel('检索问题').fill('员工每年有几天年假？')
  await page.getByRole('button', { name: /检\s*索/ }).click()
  await expect(page.getByText('演示制度规定：员工每年享有十天年假。')).toBeVisible()
  await expect(page.getByText(/员工手册.txt · 相似度 0.930/)).toBeVisible()
})
