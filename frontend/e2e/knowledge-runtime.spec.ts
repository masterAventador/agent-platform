import { expect, test } from '@playwright/test'

import { registerAndLogin } from './helpers/auth'


const expectedOutput = process.env.PLAYWRIGHT_RUNTIME_EXPECTED_OUTPUT
  ?? 'Runtime E2E completed in the real worker.'

test('发布绑定知识库的员工后任务详情展示知识检索引用', async ({ page }) => {
  await registerAndLogin(page)

  await page.getByRole('link', { name: '知识库' }).click()
  await page.getByRole('button', { name: '创建知识库' }).click()
  await page.getByLabel('知识库名称').fill('运行时制度库')
  await page.getByLabel('说明').fill('运行时引用验收')
  await page.getByRole('dialog').getByRole('button', { name: /创\s*建/ }).click()
  await expect(page).toHaveURL(/\/knowledge-bases\/[0-9a-f-]+$/)

  await page.getByLabel('选择文档').setInputFiles({
    name: '运行时员工手册.txt',
    mimeType: 'text/plain',
    buffer: Buffer.from('员工每年享有十天年假。'),
  })
  await page.getByRole('button', { name: '上传并解析' }).click()
  await expect(page.getByText('运行时员工手册.txt', { exact: true })).toBeVisible()
  await expect(page.getByText('解析完成')).toBeVisible()

  await page.getByRole('link', { name: '数字员工' }).click()
  await page.getByRole('button', { name: '创建数字员工' }).click()
  await page.getByLabel('员工名称').fill('知识引用验收专员')
  await page.getByLabel('岗位说明').fill('结合知识库回答制度问题')
  await page.getByLabel('系统指令').fill(`引用知识库内容回答，固定回复：${expectedOutput}`)
  await page.getByRole('combobox', { name: '知识库' }).click()
  await page.getByRole('option', { name: '运行时制度库' }).click()

  await expect(page.getByText('知识检索配置')).toBeVisible()
  const pageSize = page.getByRole('spinbutton', { name: '召回条数' })
  await pageSize.fill('3')
  await page.getByRole('checkbox', { name: '关键词增强' }).check()

  await page.getByRole('button', { name: '保存草稿' }).click()
  await expect(page).toHaveURL(/\/employees\/[0-9a-f-]+$/)
  await page.getByRole('button', { name: '发布员工' }).click()
  await expect(page.getByText('已发布', { exact: true })).toBeVisible()

  await page.getByRole('button', { name: '发起任务' }).click()
  await page.getByLabel('任务内容').fill('员工每年有几天年假？')
  await page.getByRole('button', { name: '确认发起' }).click()

  await expect(page).toHaveURL(/\/runs\/[0-9a-f-]+$/)
  await expect(page.getByText('已完成', { exact: true })).toBeVisible()
  await expect(page.getByText('检索知识库引用', { exact: true })).toBeVisible()
  await expect(page.getByText('引用 1 个知识片段')).toBeVisible()
  await expect(page.getByText('运行时员工手册.txt', { exact: true })).toBeVisible()
  await expect(page.getByText('演示制度规定：员工每年享有十天年假。')).toBeVisible()
  await expect(page.getByText(expectedOutput, { exact: true })).toBeVisible()

  await page.reload()
  await expect(page.getByText('检索知识库引用', { exact: true })).toBeVisible()
  await expect(page.getByText('运行时员工手册.txt', { exact: true })).toBeVisible()
})
