import { execFileSync } from 'node:child_process'
import { existsSync, readFileSync, rmSync } from 'node:fs'
import { resolve } from 'node:path'

import {
  composeArgs,
  composeArgsForProject,
  composeEnvironment,
  repositoryRoot,
} from './helpers/compose-core'

const ownershipMarker = resolve(repositoryRoot, '.local/playwright-owned-core')

export default function globalTeardown() {
  try {
    execFileSync(
      'docker',
      [...composeArgs, 'exec', '-T', 'postgres', 'dropdb', '--force', '--if-exists', '-U', 'agent_platform', 'agent_platform_e2e'],
      { cwd: repositoryRoot, env: composeEnvironment, stdio: 'inherit' },
    )
  } finally {
    if (existsSync(ownershipMarker)) {
      const marker = JSON.parse(readFileSync(ownershipMarker, 'utf8')) as
        | string[]
        | { projectName?: string; startedServices: string[]; removeProjectOnTeardown?: boolean }
      const startedServices = Array.isArray(marker) ? marker : marker.startedServices
      const teardownComposeArgs = Array.isArray(marker) || !marker.projectName
        ? composeArgs
        : composeArgsForProject(marker.projectName)
      if (!Array.isArray(marker) && marker.removeProjectOnTeardown) {
        execFileSync('docker', [...teardownComposeArgs, 'down', '-v'], {
          cwd: repositoryRoot,
          env: composeEnvironment,
          stdio: 'inherit',
        })
      } else {
        execFileSync('docker', [...teardownComposeArgs, 'stop', ...startedServices], {
          cwd: repositoryRoot,
          env: composeEnvironment,
          stdio: 'inherit',
        })
      }
      rmSync(ownershipMarker, { force: true })
    }
  }
}
