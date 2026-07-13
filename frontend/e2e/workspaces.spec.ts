import { expect, test } from '@playwright/test'

import { registerAndLogin } from './helpers/auth'
import { prepareWorkspaceFixture, selectWorkspace } from './helpers/workspaces'


test('真实工作区切换隔离数据、权限与持久选择', async ({ page }) => {
  const email = await registerAndLogin(page)
  const fixture = prepareWorkspaceFixture(email)
  await page.reload()

  await expect(page.getByLabel('当前工作区').locator('..')).toContainText(
    fixture.owner_workspace_name,
  )
  await page.getByRole('link', { name: '数字员工' }).click()
  await expect(page.getByText(fixture.owner_employee_name, { exact: true })).toBeVisible()
  await expect(page.getByText(fixture.member_employee_name, { exact: true })).toHaveCount(0)
  await expect(page.getByRole('link', { name: '任务运维' })).toBeVisible()

  await selectWorkspace(page, fixture.member_workspace_name)
  await expect(page.getByRole('heading', { name: '工作台' })).toBeVisible()
  await expect(page.getByRole('link', { name: '任务运维' })).toHaveCount(0)
  await page.getByRole('link', { name: '数字员工' }).click()
  await expect(page.getByText(fixture.member_employee_name, { exact: true })).toBeVisible()
  await expect(page.getByText(fixture.owner_employee_name, { exact: true })).toHaveCount(0)

  await page.goto(`/employees/${fixture.member_employee_id}/edit`)
  await expect(page.getByText('无权编辑数字员工', { exact: true })).toBeVisible()
  await expect(page.getByText('仅工作区所有者可以创建或编辑数字员工。')).toBeVisible()
  await expect(page.getByRole('heading', { name: '编辑数字员工' })).toHaveCount(0)

  await page.reload()
  await expect(page.getByLabel('当前工作区').locator('..')).toContainText(
    fixture.member_workspace_name,
  )
  await expect(page.getByRole('link', { name: '任务运维' })).toHaveCount(0)

  await selectWorkspace(page, fixture.owner_workspace_name)
  await expect(page.getByRole('heading', { name: '工作台' })).toBeVisible()
  await expect(page.getByRole('link', { name: '任务运维' })).toBeVisible()
  await page.getByRole('link', { name: '数字员工' }).click()
  await expect(page.getByText(fixture.owner_employee_name, { exact: true })).toBeVisible()
  await expect(page.getByText(fixture.member_employee_name, { exact: true })).toHaveCount(0)
})

test('owner 创建员工时只能选择已接通的真实配置', async ({ page }) => {
  const email = await registerAndLogin(page)
  prepareWorkspaceFixture(email)
  await page.reload()

  await page.getByRole('link', { name: '数字员工' }).click()
  await page.getByRole('button', { name: '创建数字员工' }).click()
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
})
