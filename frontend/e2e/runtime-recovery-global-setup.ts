import { existsSync } from 'node:fs'

import {
  composeExec,
  queryRecoveryDatabase,
  recoveryQueueStream,
} from './helpers/runtime-infra'


export default async function runtimeRecoveryGlobalSetup() {
  const schemaVersion = queryRecoveryDatabase('select version_num from alembic_version')
  if (!process.env.PLAYWRIGHT_RUNTIME_EXPECTED_SCHEMA_VERSION
      || schemaVersion !== process.env.PLAYWRIGHT_RUNTIME_EXPECTED_SCHEMA_VERSION) {
    throw new Error(`Runtime recovery E2E schema is not at head: ${schemaVersion}`)
  }
  const counts = queryRecoveryDatabase(
    'select (select count(*) from users)::text || \'|\' || (select count(*) from runs)::text || \'|\' || (select count(*) from sandbox_leases)::text',
  )
  if (counts !== '0|0|0') throw new Error(`Runtime recovery database is not clean: ${counts}`)
  const queueLength = composeExec('redis', [
    'redis-cli', '-a', 'agent-platform-local-redis', '-n', '4', 'XLEN', recoveryQueueStream,
  ])
  if (queueLength !== '0') throw new Error(`Runtime recovery queue is not clean: ${queueLength}`)
  const readyFiles = [
    '/tmp/agent-platform-runtime-recovery-e2e-dispatcher-ready',
    '/tmp/agent-platform-runtime-recovery-e2e-supervisor-ready',
    '/tmp/agent-platform-runtime-recovery-e2e-worker-ready',
  ]
  const deadline = Date.now() + 120_000
  while (Date.now() < deadline && readyFiles.some((path) => !existsSync(path))) {
    await new Promise((resolve) => setTimeout(resolve, 100))
  }
  const missing = readyFiles.filter((path) => !existsSync(path))
  if (missing.length) {
    throw new Error(`Runtime recovery processes are not ready: ${missing.join(', ')}`)
  }
}
