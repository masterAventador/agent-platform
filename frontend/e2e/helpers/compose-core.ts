import { dirname, resolve } from 'node:path'
import { fileURLToPath } from 'node:url'

export const frontendRoot = resolve(dirname(fileURLToPath(import.meta.url)), '../..')
export const repositoryRoot = resolve(frontendRoot, '..')
export const composeFile = resolve(repositoryRoot, 'infra/compose/core.yml')
export const composeEnv = resolve(repositoryRoot, 'infra/compose/.env.example')

function readEnv(primary: string, legacy: string, fallback: string): string {
  return process.env[primary] ?? process.env[legacy] ?? fallback
}

export const composeProjectName =
  process.env.PLAYWRIGHT_COMPOSE_PROJECT_NAME
  ?? process.env.COMPOSE_PROJECT_NAME
  ?? 'agent-platform-playwright'
export const postgresPort = readEnv('PLAYWRIGHT_POSTGRES_PORT', 'POSTGRES_PORT', '5432')
export const redisPort = readEnv('PLAYWRIGHT_REDIS_PORT', 'REDIS_PORT', '6379')
export const minioApiPort = readEnv('PLAYWRIGHT_MINIO_API_PORT', 'MINIO_API_PORT', '9000')
export const minioConsolePort = readEnv(
  'PLAYWRIGHT_MINIO_CONSOLE_PORT',
  'MINIO_CONSOLE_PORT',
  '9001',
)

export function composeArgsForProject(projectName: string = composeProjectName): string[] {
  return ['compose', '--project-name', projectName, '--env-file', composeEnv, '-f', composeFile]
}

export const composeArgs = composeArgsForProject()

export const composeEnvironment: NodeJS.ProcessEnv = {
  ...process.env,
  COMPOSE_PROJECT_NAME: composeProjectName,
  POSTGRES_PORT: postgresPort,
  REDIS_PORT: redisPort,
  MINIO_API_PORT: minioApiPort,
  MINIO_CONSOLE_PORT: minioConsolePort,
}

export function postgresDatabaseUrl(databaseName: string): string {
  return `postgresql+asyncpg://agent_platform:agent-platform-local-postgres@127.0.0.1:${postgresPort}/${databaseName}`
}

export function redisDatabaseUrl(databaseNumber: number): string {
  return `redis://:agent-platform-local-redis@127.0.0.1:${redisPort}/${databaseNumber}`
}
