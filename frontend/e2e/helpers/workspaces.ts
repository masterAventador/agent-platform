import { execFileSync } from 'node:child_process'
import { dirname, resolve } from 'node:path'
import { fileURLToPath } from 'node:url'
import type { Page } from '@playwright/test'


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

const frontendRoot = resolve(dirname(fileURLToPath(import.meta.url)), '../..')
const backendRoot = resolve(frontendRoot, '../backend')
const databaseUrl =
  'postgresql+asyncpg://agent_platform:agent-platform-local-postgres@127.0.0.1:5432/agent_platform_e2e'

export function prepareWorkspaceFixture(email: string): WorkspaceFixture {
  const output = execFileSync(
    'uv',
    ['run', 'python', '-m', 'tests.fixtures.workspace_e2e', email],
    {
      cwd: backendRoot,
      encoding: 'utf8',
      env: { ...process.env, AGENT_PLATFORM_DATABASE_URL: databaseUrl },
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
