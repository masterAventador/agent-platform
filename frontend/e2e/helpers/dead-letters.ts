import { execFileSync } from 'node:child_process'
import { dirname, resolve } from 'node:path'
import { fileURLToPath } from 'node:url'


export const validPayloadMarker = 'valid-payload-must-never-appear'
export const malformedPayloadMarker = 'malformed-payload-must-never-appear'
export const rawFieldValueMarker = 'raw-field-value-must-never-appear'

export type DeadLetterFixture = {
  tenant_id: string
  valid_dead_letter_id: string
  malformed_dead_letter_id: string
  original_run_id: string
  malformed_run_id: string
}

const frontendRoot = resolve(dirname(fileURLToPath(import.meta.url)), '../..')
const backendRoot = resolve(frontendRoot, '../backend')
const databaseUrl =
  `postgresql+asyncpg://agent_platform:agent-platform-local-postgres@127.0.0.1:${process.env.POSTGRES_PORT ?? '5432'}/agent_platform_e2e`

export function prepareDeadLetterFixture(ownerEmail: string): DeadLetterFixture {
  const output = execFileSync(
    'uv',
    ['run', 'python', '-m', 'tests.fixtures.dead_letter_e2e', ownerEmail],
    {
      cwd: backendRoot,
      encoding: 'utf8',
      env: { ...process.env, AGENT_PLATFORM_DATABASE_URL: databaseUrl },
    },
  )
  return JSON.parse(output) as DeadLetterFixture
}

export function setDeadLetterWorkspaceRole(
  email: string,
  role: 'admin' | 'member',
): void {
  execFileSync(
    'uv',
    ['run', 'python', '-m', 'tests.fixtures.dead_letter_e2e', 'set-role', email, role],
    {
      cwd: backendRoot,
      stdio: 'inherit',
      env: { ...process.env, AGENT_PLATFORM_DATABASE_URL: databaseUrl },
    },
  )
}
