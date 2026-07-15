import { execFileSync } from 'node:child_process'
import { existsSync, mkdirSync, readFileSync, rmSync, writeFileSync } from 'node:fs'
import { dirname, resolve } from 'node:path'

import {
  composeArgs,
  composeEnvironment,
  composeProjectName,
  postgresDatabaseUrl,
  repositoryRoot,
} from './helpers/compose-core'

export const databaseUrl = postgresDatabaseUrl('agent_platform_e2e')
const ownershipMarker = resolve(repositoryRoot, '.local/playwright-owned-core')

export default function globalSetup() {
  try {
    rmSync(ownershipMarker, { force: true })
    const services = ['postgres', 'redis', 'minio']
    const startedServices = services.filter((service) => !execFileSync(
      'docker',
      [...composeArgs, 'ps', '-q', service],
      { cwd: repositoryRoot, encoding: 'utf8', env: composeEnvironment },
    ).trim())
    if (startedServices.length) {
      mkdirSync(dirname(ownershipMarker), { recursive: true })
      writeFileSync(ownershipMarker, JSON.stringify({
        projectName: composeProjectName,
        startedServices,
      }))
    }
    execFileSync('docker', [...composeArgs, 'up', '-d', '--wait', ...services], {
      cwd: repositoryRoot,
      env: composeEnvironment,
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
      { cwd: repositoryRoot, env: composeEnvironment, stdio: 'inherit' },
    )
    execFileSync(
      'docker',
      [...composeArgs, 'exec', '-T', 'postgres', 'dropdb', '--force', '--if-exists', '-U', 'agent_platform', 'agent_platform_e2e'],
      { cwd: repositoryRoot, env: composeEnvironment, stdio: 'inherit' },
    )
    execFileSync(
      'docker',
      [...composeArgs, 'exec', '-T', 'postgres', 'createdb', '-U', 'agent_platform', 'agent_platform_e2e'],
      { cwd: repositoryRoot, env: composeEnvironment, stdio: 'inherit' },
    )
    execFileSync('uv', ['run', 'alembic', 'upgrade', 'head'], {
      cwd: resolve(repositoryRoot, 'backend'),
      env: { ...composeEnvironment, AGENT_PLATFORM_DATABASE_URL: databaseUrl },
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
    const marker = JSON.parse(readFileSync(ownershipMarker, 'utf8')) as
      | string[]
      | { startedServices: string[] }
    const startedServices = Array.isArray(marker) ? marker : marker.startedServices
    execFileSync('docker', [...composeArgs, 'stop', ...startedServices], {
      cwd: repositoryRoot,
      env: composeEnvironment,
      stdio: 'inherit',
    })
  } catch {
    // 标记不存在时没有本轮启动的容器需要停止。
  } finally {
    rmSync(ownershipMarker, { force: true })
  }
}
