import { execFileSync } from 'node:child_process'
import { dirname, resolve } from 'node:path'
import { fileURLToPath } from 'node:url'


const frontendRoot = resolve(dirname(fileURLToPath(import.meta.url)), '..')
const repositoryRoot = resolve(frontendRoot, '..')
const composeFile = resolve(repositoryRoot, 'infra/compose/core.yml')
const composeEnv = resolve(repositoryRoot, 'infra/compose/.env.example')
const composeArgs = ['compose', '--env-file', composeEnv, '-f', composeFile]

export default function globalTeardown() {
  try {
    execFileSync(
      'docker',
      [...composeArgs, 'exec', '-T', 'postgres', 'dropdb', '--force', '--if-exists', '-U', 'agent_platform', 'agent_platform_e2e'],
      { cwd: repositoryRoot, stdio: 'inherit' },
    )
  } finally {
    execFileSync('docker', [...composeArgs, 'down'], {
      cwd: repositoryRoot,
      stdio: 'inherit',
    })
  }
}
