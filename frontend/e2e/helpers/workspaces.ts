import { execFileSync } from 'node:child_process'
import { resolve } from 'node:path'
import type { Page } from '@playwright/test'

import { composeEnvironment, frontendRoot, postgresDatabaseUrl } from './compose-core'

export interface WorkspaceFixture {
  owner_workspace_id: string
  owner_workspace_name: string
  member_workspace_id: string
  member_workspace_name: string
  owner_employee_id: string
  owner_employee_name: string
  member_employee_id: string
  member_employee_name: string
}

const backendRoot = resolve(frontendRoot, '../backend')
const databaseUrl = postgresDatabaseUrl('agent_platform_e2e')

export function prepareWorkspaceFixture(email: string): WorkspaceFixture {
  const output = execFileSync(
    'uv',
    ['run', 'python', '-m', 'tests.fixtures.workspace_e2e', email],
    {
      cwd: backendRoot,
      encoding: 'utf8',
      env: { ...composeEnvironment, AGENT_PLATFORM_DATABASE_URL: databaseUrl },
    },
  )
  return JSON.parse(output) as WorkspaceFixture
}

export async function selectWorkspace(page: Page, workspaceName: string): Promise<void> {
  await page.getByLabel('当前工作区').click()
  await page.locator('.ant-select-dropdown:visible').getByText(workspaceName, {
    exact: true,
  }).click()
}
