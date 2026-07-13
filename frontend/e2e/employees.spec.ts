import { expect, test } from '@playwright/test'

import { registerAndLogin } from './helpers/auth'


test('用户可以创建并发布数字员工', async ({ page }) => {
  await registerAndLogin(page)

  await page.getByRole('link', { name: '数字员工' }).click()
  await expect(page).toHaveURL(/\/employees$/)
  await expect(page.getByText('还没有数字员工')).toBeVisible()

  await page.getByRole('button', { name: '创建数字员工' }).click()
  await page.getByLabel('员工名称').fill('市场研究专员')
  await page.getByLabel('岗位说明').fill('负责市场信息收集、竞品分析和研究报告整理')
  await page.getByLabel('工作模式').click()
  await expect(page.getByRole('option', { name: '自主执行' })).toHaveAttribute(
    'aria-selected',
    'true',
  )
  await expect(page.getByRole('option', { name: '固定流程（尚未开放）' })).toHaveAttribute(
    'aria-disabled',
    'true',
  )
  await expect(page.getByRole('option', { name: '混合协作（尚未开放）' })).toHaveAttribute(
    'aria-disabled',
    'true',
  )
  await page.keyboard.press('Escape')
  await expect(page.getByRole('checkbox', { name: '支持对话' })).toBeEnabled()
  await expect(page.getByRole('checkbox', { name: '支持文件上传（尚未接通）' })).toBeDisabled()
  await expect(page.getByRole('checkbox', { name: '支持文件上传（尚未接通）' })).not.toBeChecked()
  await expect(page.getByRole('checkbox', { name: '支持定时任务（尚未接通）' })).toBeDisabled()
  await expect(page.getByRole('checkbox', { name: '支持定时任务（尚未接通）' })).not.toBeChecked()
  await page.getByLabel('系统指令').fill('核实信息来源后，输出结构化市场研究报告。')
  await page.getByRole('button', { name: '保存草稿' }).click()

  await expect(page).toHaveURL(/\/employees\/[0-9a-f-]+$/)
  await expect(page.getByRole('heading', { name: '市场研究专员' })).toBeVisible()
  await expect(page.getByText('草稿', { exact: true })).toBeVisible()

  await page.getByRole('button', { name: '发布员工' }).click()
  await expect(page.getByText('已发布', { exact: true })).toBeVisible()
  await expect(page.getByText('版本 1')).toBeVisible()

  await page.getByRole('link', { name: '数字员工' }).click()
  await expect(page.getByText('市场研究专员')).toBeVisible()
  await expect(page.getByText('已发布', { exact: true })).toBeVisible()
})
