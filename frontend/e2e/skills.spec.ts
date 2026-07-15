import { expect, test } from '@playwright/test'

import { registerAndLogin } from './helpers/auth'


const initialSkillBundle = Buffer.from(
  'UEsDBBQAAAAIADsA7Vy8N+POggAAAI8AAAAIAAAAU0tJTEwubWTT1dXlykvMTbVSKEotTk0sSs7QTSwuziwuScwr4UpJLU4uyiwoyczPs1J4trH9WcPu5wumPF+57cXWlmfTZj7due35lPnPOiY83bHs2dylz3ZNeD6r5dnEGS+WNXLpAs3lUlYIghqq4Ag3lOvF+u1PWzueLdjxdN08iEEQzY8bmrgAUEsDBBQAAAAIADsA7VwEBc2VMwAAAC4AAAAXAAAAcmVmZXJlbmNlcy9jaGVja2xpc3QubWQBLgDR/yMg5qC46aqM5riF5Y2VCgotIOiusOW9leadpea6kAotIOagh+azqOaXpeacnwpQSwECFAMUAAAACAA7AO1cvDfjzoIAAACPAAAACAAAAAAAAAAAAAAAgAEAAAAAU0tJTEwubWRQSwECFAMUAAAACAA7AO1cBAXNlTMAAAAuAAAAFwAAAAAAAAAAAAAAgAGoAAAAcmVmZXJlbmNlcy9jaGVja2xpc3QubWRQSwUGAAAAAAIAAgB7AAAAEAEAAAAA',
  'base64',
)
const secondSkillBundle = Buffer.from(
  'UEsDBBQAAAAIAEIA7VwFq+8ijQAAAJUAAAAIAAAAU0tJTEwubWTT1dXlykvMTbVSKEotTk0sSs7QTSwuziwuScwr4UpJLU4uyiwoyczPs1J4vmbNk109zzs7nk1Z/6yn8VlP57PpS5/Nmf9swY6Xq3qeL5jyfOW2F1tbnk2byaULNJRLWSEIaqKCI8xEhTIjLq4X67cD9TxdN+/Z3KXPdk14Oqnnaf/EpzuaIcY9bmjiAgBQSwMEFAAAAAgAQgDtXALI+QM8AAAANwAAABcAAAByZWZlcmVuY2VzL2NoZWNrbGlzdC5tZAE3AMj/IyDmoLjpqozmuIXljZUgdjIKCi0g6K6w5b2V5p2l5rqQCi0g5qCH5rOo5Y+R5biD5pel5pyfClBLAQIUAxQAAAAIAEIA7VwFq+8ijQAAAJUAAAAIAAAAAAAAAAAAAACAAQAAAABTS0lMTC5tZFBLAQIUAxQAAAAIAEIA7VwCyPkDPAAAADcAAAAXAAAAAAAAAAAAAACAAbMAAAByZWZlcmVuY2VzL2NoZWNrbGlzdC5tZFBLBQYAAAAAAgACAHsAAAAkAQAAAAA=',
  'base64',
)

test('用户可以管理 Skill 版本并将已发布 Skill 绑定到数字员工', async ({ page }) => {
  await registerAndLogin(page)

  await page.getByRole('link', { name: 'Skill 中心' }).click()
  await expect(page).toHaveURL(/\/skills$/)
  await expect(page.getByText('还没有 Skill')).toBeVisible()

  await page.getByRole('button', { name: '上传 Skill' }).click()
  await page.getByLabel('Skill ZIP').setInputFiles({
    name: 'research-assistant.zip',
    mimeType: 'application/zip',
    buffer: initialSkillBundle,
  })
  await page.getByRole('dialog').getByRole('button', { name: '创建 Skill' }).click()

  await expect(page).toHaveURL(/\/skills\/[0-9a-f-]+$/)
  await expect(page.getByRole('heading', { name: 'research-assistant' })).toBeVisible()
  await expect(page.getByText('汇总研究资料并生成带来源的摘要').first()).toBeVisible()
  await expect(page.getByText('版本 1', { exact: true }).first()).toBeVisible()
  await expect(page.getByRole('heading', { name: '安全审核结果' })).toBeVisible()
  await expect(page.getByText('安全审核通过').first()).toBeVisible()
  await expect(page.getByRole('button', { name: 'SKILL.md' })).toBeVisible()
  await expect(page.getByText('# Research Assistant')).toBeVisible()

  await page.getByRole('button', { name: 'references/checklist.md' }).click()
  await expect(page.getByText('# 核验清单')).toBeVisible()

  await page.getByRole('button', { name: '上传新版本' }).click()
  await page.getByLabel('新版本 ZIP').setInputFiles({
    name: 'research-assistant-v2.zip',
    mimeType: 'application/zip',
    buffer: secondSkillBundle,
  })
  await page.getByRole('dialog').getByRole('button', { name: '上传版本' }).click()

  await expect(page.getByText('版本 2', { exact: true }).first()).toBeVisible()
  await expect(page.getByText('第二版支持按日期核验研究资料').first()).toBeVisible()
  await expect(page.getByRole('heading', { name: '版本差异' })).toBeVisible()
  await expect(page.getByText('references/checklist.md')).toBeVisible()
  await page.getByRole('button', { name: '发布版本 2' }).click()
  await expect(page.getByText('已发布版本 2')).toBeVisible()
  await expect(page.getByRole('button', { name: '下线 Skill' })).toBeVisible()

  await page.getByRole('link', { name: '数字员工' }).click()
  await page.getByRole('button', { name: '创建数字员工' }).click()
  await page.getByLabel('员工名称').fill('研究专员')
  await page.getByLabel('岗位说明').fill('负责核验并汇总研究资料')
  await page.getByLabel('系统指令').fill('使用已发布的研究 Skill 完成任务。')
  await page.getByLabel('Skills').click()
  await page.getByText('research-assistant（版本 2）', { exact: true }).last().click()
  await page.keyboard.press('Escape')
  await page.getByRole('button', { name: '保存草稿' }).click()

  await expect(page).toHaveURL(/\/employees\/[0-9a-f-]+$/)
  await page.getByRole('button', { name: /编\s*辑/ }).click()
  await expect(page.getByText('research-assistant（版本 2）', { exact: true })).toBeVisible()
  await page.getByLabel('岗位说明').fill('负责核验、汇总并输出研究资料')
  await page.getByRole('button', { name: '保存草稿' }).click()
  await page.getByRole('button', { name: /编\s*辑/ }).click()
  await expect(page.getByText('research-assistant（版本 2）', { exact: true })).toBeVisible()
})
