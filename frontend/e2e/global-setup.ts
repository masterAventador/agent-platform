import { execFileSync } from 'node:child_process'
import { dirname, resolve } from 'node:path'
import { fileURLToPath } from 'node:url'


const frontendRoot = resolve(dirname(fileURLToPath(import.meta.url)), '..')
const repositoryRoot = resolve(frontendRoot, '..')
const composeFile = resolve(repositoryRoot, 'infra/compose/core.yml')
const composeEnv = resolve(repositoryRoot, 'infra/compose/.env.example')
const composeArgs = ['compose', '--env-file', composeEnv, '-f', composeFile]
const databaseUrl =
  'postgresql+asyncpg://agent_platform:agent-platform-local-postgres@127.0.0.1:5432/agent_platform_e2e'

export default function globalSetup() {
  try {
    execFileSync('docker', [...composeArgs, 'up', '-d', '--wait', 'postgres', 'redis'], {
      cwd: repositoryRoot,
      stdio: 'inherit',
    })
    execFileSync(
      'docker',
      [...composeArgs, 'exec', '-T', 'postgres', 'dropdb', '--force', '--if-exists', '-U', 'agent_platform', 'agent_platform_e2e'],
      { cwd: repositoryRoot, stdio: 'inherit' },
    )
    execFileSync(
      'docker',
      [...composeArgs, 'exec', '-T', 'postgres', 'createdb', '-U', 'agent_platform', 'agent_platform_e2e'],
      { cwd: repositoryRoot, stdio: 'inherit' },
    )
    execFileSync('uv', ['run', 'alembic', 'upgrade', 'head'], {
      cwd: resolve(repositoryRoot, 'backend'),
      env: { ...process.env, AGENT_PLATFORM_DATABASE_URL: databaseUrl },
      stdio: 'inherit',
    })
  } catch (error) {
    execFileSync('docker', [...composeArgs, 'down'], {
      cwd: repositoryRoot,
      stdio: 'inherit',
    })
    throw error
  }
}
