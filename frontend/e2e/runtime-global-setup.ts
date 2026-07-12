import { existsSync } from 'node:fs'

import {
  composeExec,
  queryRuntimeDatabase,
  runtimeQueueStream,
  runtimeReadyFiles,
} from './helpers/runtime-infra'


async function waitForRuntimeProcesses(): Promise<void> {
  const deadline = Date.now() + 120_000
  while (Date.now() < deadline) {
    if (runtimeReadyFiles.every((path) => existsSync(path))) return
    await new Promise((resolve) => setTimeout(resolve, 100))
  }
  const missing = runtimeReadyFiles.filter((path) => !existsSync(path))
  throw new Error(`Runtime E2E processes did not become ready: ${missing.join(', ')}`)
}


export default async function runtimeGlobalSetup() {
  const schemaVersion = queryRuntimeDatabase('select version_num from alembic_version')
  if (schemaVersion !== '20260713_0010') {
    throw new Error(`Runtime E2E infrastructure is not prepared: ${schemaVersion}`)
  }
  const fixtureCounts = queryRuntimeDatabase(
    'select (select count(*) from users)::text || \'|\' || (select count(*) from runs)::text || \'|\' || (select count(*) from sandbox_leases)::text',
  )
  if (fixtureCounts !== '0|0|0') {
    throw new Error(`Runtime E2E database is not clean: ${fixtureCounts}`)
  }
  const queueLength = composeExec('redis', [
    'redis-cli', '-a', 'agent-platform-local-redis', '-n', '3',
    'XLEN', runtimeQueueStream,
  ])
  if (queueLength !== '0') {
    throw new Error(`Runtime E2E queue is not clean: ${queueLength}`)
  }
  await waitForRuntimeProcesses()
}
