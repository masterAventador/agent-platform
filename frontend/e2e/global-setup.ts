import { execFileSync } from 'node:child_process'
import { existsSync, mkdirSync, readFileSync, rmSync, writeFileSync } from 'node:fs'
import { dirname, resolve } from 'node:path'
import { fileURLToPath } from 'node:url'


const frontendRoot = resolve(dirname(fileURLToPath(import.meta.url)), '..')
const repositoryRoot = resolve(frontendRoot, '..')
const composeFile = resolve(repositoryRoot, 'infra/compose/core.yml')
const composeEnv = resolve(repositoryRoot, 'infra/compose/.env.example')
const composeArgs = ['compose', '--env-file', composeEnv, '-f', composeFile]
export const databaseUrl =
  `postgresql+asyncpg://agent_platform:agent-platform-local-postgres@127.0.0.1:${process.env.POSTGRES_PORT ?? '5432'}/agent_platform_e2e`
const ownershipMarker = resolve(repositoryRoot, '.local/playwright-owned-core')

export default function globalSetup() {
  try {
    rmSync(ownershipMarker, { force: true })
    const services = ['postgres', 'redis', 'minio']
    const startedServices = services.filter((service) => !execFileSync(
      'docker',
      [...composeArgs, 'ps', '-q', service],
      { cwd: repositoryRoot, encoding: 'utf8' },
    ).trim())
    if (startedServices.length) {
      mkdirSync(dirname(ownershipMarker), { recursive: true })
      writeFileSync(ownershipMarker, JSON.stringify(startedServices))
    }
    execFileSync('docker', [...composeArgs, 'up', '-d', '--wait', ...services], {
      cwd: repositoryRoot,
      stdio: 'inherit',
    })
    execFileSync(
      'docker',
      [
        ...composeArgs,
        'exec',
        '-T',
        '-e',
        'REDISCLI_AUTH=agent-platform-local-redis',
        'redis',
        'redis-cli',
        '-n',
        '2',
        'FLUSHDB',
      ],
      { cwd: repositoryRoot, stdio: 'inherit' },
    )
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
    stopOwnedServices()
    throw error
  }
}

function stopOwnedServices() {
  try {
    if (!existsSync(ownershipMarker)) return
    const startedServices = JSON.parse(readFileSync(ownershipMarker, 'utf8')) as string[]
    execFileSync('docker', [...composeArgs, 'stop', ...startedServices], {
      cwd: repositoryRoot,
      stdio: 'inherit',
    })
  } catch {
    // 标记不存在时没有本轮启动的容器需要停止。
  } finally {
    rmSync(ownershipMarker, { force: true })
  }
}
