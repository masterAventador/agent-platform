import { execFileSync } from 'node:child_process'
import { dirname, resolve } from 'node:path'
import { fileURLToPath } from 'node:url'


export const frontendRoot = resolve(dirname(fileURLToPath(import.meta.url)), '../..')
export const repositoryRoot = resolve(frontendRoot, '..')
export const runtimeDatabaseName = 'agent_platform_runtime_e2e'
export const runtimeDatabaseUrl =
  'postgresql+asyncpg://agent_platform:agent-platform-local-postgres@127.0.0.1:5432/agent_platform_runtime_e2e'
export const runtimeRedisUrl = 'redis://:agent-platform-local-redis@127.0.0.1:6379/3'
export const runtimeQueueStream = 'agent-platform:runtime-e2e:runs'
export const runtimeQueueGroup = 'agent-platform-runtime-e2e-workers'
export const runtimeControllerSecret = 'runtime-e2e-controller-secret'
export const runtimeReadyFiles = [
  '/tmp/agent-platform-runtime-e2e-dispatcher-ready',
  '/tmp/agent-platform-runtime-e2e-worker-ready',
] as const

export const recoveryDatabaseName = 'agent_platform_runtime_recovery_e2e'
export const recoveryDatabaseUrl =
  'postgresql+asyncpg://agent_platform:agent-platform-local-postgres@127.0.0.1:5432/agent_platform_runtime_recovery_e2e'
export const recoveryRedisUrl = 'redis://:agent-platform-local-redis@127.0.0.1:6379/4'
export const recoveryQueueStream = 'agent-platform:runtime-recovery-e2e:runs'
export const recoveryQueueGroup = 'agent-platform-runtime-recovery-e2e-workers'

const composeFile = resolve(repositoryRoot, 'infra/compose/core.yml')
const composeEnv = resolve(repositoryRoot, 'infra/compose/.env.example')
const composeArgs = ['compose', '--env-file', composeEnv, '-f', composeFile]

export function composeExec(service: string, args: string[]): string {
  return execFileSync('docker', [...composeArgs, 'exec', '-T', service, ...args], {
    cwd: repositoryRoot,
    encoding: 'utf8',
  }).trim()
}

export function resetRuntimeRedis(): void {
  composeExec('redis', [
    'redis-cli',
    '-a',
    'agent-platform-local-redis',
    '-n',
    '3',
    'FLUSHDB',
  ])
}

export function dropRuntimeDatabase(): void {
  composeExec('postgres', [
    'dropdb',
    '--force',
    '--if-exists',
    '-U',
    'agent_platform',
    runtimeDatabaseName,
  ])
}

export function queryRuntimeDatabase(sql: string): string {
  return composeExec('postgres', [
    'psql',
    '-U',
    'agent_platform',
    '-d',
    runtimeDatabaseName,
    '-At',
    '-v',
    'ON_ERROR_STOP=1',
    '-c',
    sql,
  ])
}

export function queryRecoveryDatabase(sql: string): string {
  return composeExec('postgres', [
    'psql', '-U', 'agent_platform', '-d', recoveryDatabaseName,
    '-At', '-v', 'ON_ERROR_STOP=1', '-c', sql,
  ])
}

export function cleanupRuntimeSandboxes(): void {
  let leaseIds = ''
  try {
    leaseIds = queryRuntimeDatabase('select id::text from sandbox_leases')
  } catch {
    return
  }
  for (const leaseId of leaseIds.split('\n').filter(Boolean)) {
    const containerIds = execFileSync(
      'docker',
      [
        'ps', '-aq',
        '--filter', 'label=agent-platform.sandbox.managed=true',
        '--filter', `label=agent-platform.sandbox.lease-id=${leaseId}`,
      ],
      { encoding: 'utf8' },
    ).trim().split('\n').filter(Boolean)
    if (containerIds.length) execFileSync('docker', ['rm', '-f', ...containerIds])
  }
}
