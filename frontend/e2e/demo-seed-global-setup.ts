import { execFileSync } from 'node:child_process'
import { dirname, resolve } from 'node:path'
import { fileURLToPath } from 'node:url'

import globalSetup, { databaseUrl } from './global-setup'
import globalTeardown from './global-teardown'


const frontendRoot = resolve(dirname(fileURLToPath(import.meta.url)), '..')

export default function demoSeedGlobalSetup() {
  globalSetup()
  try {
    execFileSync(
      'uv',
      ['run', 'python', '-m', 'agent_platform.bootstrap.demo_seed'],
      {
        cwd: resolve(frontendRoot, '../backend'),
        env: {
          ...process.env,
          AGENT_PLATFORM_APP_ENVIRONMENT: 'development',
          AGENT_PLATFORM_DATABASE_URL: databaseUrl,
        },
        stdio: 'inherit',
      },
    )
  } catch (error) {
    globalTeardown()
    throw error
  }
}
