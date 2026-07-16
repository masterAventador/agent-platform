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
  await expect(page.getByText('员工手册.txt', { exact: true })).toBeVisible()
  await expect(page.getByText('解析完成')).toBeVisible()

  await page.getByLabel('检索问题').fill('员工每年有几天年假？')
  await page.getByRole('button', { name: /检\s*索/ }).click()
  await expect(page.getByText('演示制度规定：员工每年享有十天年假。')).toBeVisible()
  await expect(page.getByText(/员工手册.txt · 相似度 0.930/)).toBeVisible()
})

test('用户可以批量上传文档并对失败文档重试、替换和删除', async ({ page }) => {
  await registerAndLogin(page)
  await page.getByRole('link', { name: '知识库' }).click()
  await page.getByRole('button', { name: '创建知识库' }).click()
  await page.getByLabel('知识库名称').fill('文档生命周期库')
  await page.getByLabel('说明').fill('验证批量上传、重试、替换与删除')
  await page.getByRole('dialog').getByRole('button', { name: /创\s*建/ }).click()
  await expect(page).toHaveURL(/\/knowledge-bases\/[0-9a-f-]+$/)

  await page.getByLabel('选择文档').setInputFiles([
    {
      name: '正常制度.txt',
      mimeType: 'text/plain',
      buffer: Buffer.from('员工每年享有十天年假。'),
    },
    {
      name: 'parse-fail-考勤制度.txt',
      mimeType: 'text/plain',
      buffer: Buffer.from('考勤制度初稿。'),
    },
  ])
  await page.getByRole('button', { name: '批量上传并解析' }).click()
  await expect(page.getByText('正常制度.txt', { exact: true })).toBeVisible()
  await expect(page.getByText('parse-fail-考勤制度.txt', { exact: true })).toBeVisible()
  await expect(page.getByText('解析完成')).toBeVisible()
  await expect(page.getByText('解析失败')).toBeVisible()

  await page.getByRole('button', { name: '重试解析 parse-fail-考勤制度.txt' }).click()
  await expect(page.getByText('解析失败')).toHaveCount(0)
  await expect(page.getByText('解析完成')).toHaveCount(2)

  await page.getByLabel('选择替换文档 正常制度.txt').setInputFiles({
    name: '修订制度.txt',
    mimeType: 'text/plain',
    buffer: Buffer.from('员工每年享有十五天年假。'),
  })
  await expect(page.getByText('修订制度.txt', { exact: true })).toBeVisible()
  await expect(page.getByText('正常制度.txt', { exact: true })).toHaveCount(0)

  await page.getByRole('button', { name: '删除文档 parse-fail-考勤制度.txt' }).click()
  await expect(page.getByText('parse-fail-考勤制度.txt', { exact: true })).toHaveCount(0)
  await expect(page.getByText('修订制度.txt', { exact: true })).toBeVisible()

  await page.getByLabel('检索问题').fill('员工每年有几天年假？')
  await page.getByRole('button', { name: /检\s*索/ }).click()
  await expect(page.getByText(/修订制度.txt · 相似度/)).toBeVisible()
})
