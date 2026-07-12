import { defineConfig } from '@playwright/test'

import {
  frontendRoot,
  recoveryDatabaseUrl,
  recoveryQueueGroup,
  recoveryQueueStream,
  recoveryRedisUrl,
  repositoryRoot,
  runtimeControllerSecret,
} from './e2e/helpers/runtime-infra'


const sandboxImage = process.env.SANDBOX_CONTROLLER_IMAGE
  ?? 'python:3.12.13-slim-bookworm@sha256:8a7e7cc04fd3e2bd787f7f24e22d5d119aa590d429b50c95dfe12b3abe52f48b'
const backendEnvironment = {
  AGENT_PLATFORM_DATABASE_URL: recoveryDatabaseUrl,
  AGENT_PLATFORM_REDIS_URL: recoveryRedisUrl,
  AGENT_PLATFORM_RUN_QUEUE_STREAM_NAME: recoveryQueueStream,
  AGENT_PLATFORM_RUN_QUEUE_GROUP_NAME: recoveryQueueGroup,
}

export default defineConfig({
  testDir: './e2e',
  testMatch: 'runtime-recovery.spec.ts',
  globalSetup: './e2e/runtime-recovery-global-setup.ts',
  globalTeardown: './e2e/runtime-recovery-global-teardown.ts',
  fullyParallel: false,
  workers: 1,
  retries: 0,
  reporter: 'list',
  timeout: 240_000,
  expect: { timeout: 120_000 },
  use: {
    baseURL: process.env.PLAYWRIGHT_RUNTIME_RECOVERY_BASE_URL ?? 'http://127.0.0.1:15175',
    channel: 'chrome',
    trace: 'retain-on-failure',
  },
  webServer: [
    {
      command: 'uv run uvicorn agent_platform.sandbox.controller.main:app --host 127.0.0.1 --port 18091',
      cwd: `${repositoryRoot}/backend`,
      env: {
        SANDBOX_CONTROLLER_BEARER_SECRET: runtimeControllerSecret,
        SANDBOX_CONTROLLER_IMAGE: sandboxImage,
      },
      url: 'http://127.0.0.1:18091/health/ready',
      reuseExistingServer: false,
      timeout: 120_000,
    },
    {
      command: 'uv run python -m tests.fixtures.runtime_recovery_mcp',
      cwd: `${repositoryRoot}/backend`,
      env: { RUNTIME_RECOVERY_MCP_PORT: '18093' },
      url: 'http://127.0.0.1:18093/health',
      reuseExistingServer: false,
      timeout: 120_000,
    },
    {
      command: 'uv run uvicorn agent_platform.api.app:app --host 127.0.0.1 --port 18002',
      cwd: `${repositoryRoot}/backend`,
      env: {
        ...backendEnvironment,
        AGENT_PLATFORM_AUTH_REGISTER_LIMIT_PER_MINUTE: '100',
        AGENT_PLATFORM_AUTH_LOGIN_LIMIT_PER_MINUTE: '100',
      },
      url: 'http://127.0.0.1:18002/api/v1/health/live',
      reuseExistingServer: false,
      timeout: 120_000,
    },
    {
      command: 'uv run python -m tests.fixtures.runtime_dispatcher',
      cwd: `${repositoryRoot}/backend`,
      env: {
        ...backendEnvironment,
        RUNTIME_E2E_DISPATCHER_READY_FILE:
          '/tmp/agent-platform-runtime-recovery-e2e-dispatcher-ready',
      },
      reuseExistingServer: false,
      timeout: 120_000,
    },
    {
      command: 'uv run python -m tests.fixtures.runtime_recovery_supervisor',
      cwd: `${repositoryRoot}/backend`,
      env: {
        ...backendEnvironment,
        AGENT_PLATFORM_MINIO_ENDPOINT: '127.0.0.1:9000',
        AGENT_PLATFORM_MINIO_ACCESS_KEY: 'agent_platform',
        AGENT_PLATFORM_MINIO_SECRET_KEY: 'agent-platform-local-minio',
        AGENT_PLATFORM_SANDBOX_CONTROLLER_URL: 'http://127.0.0.1:18091',
        AGENT_PLATFORM_SANDBOX_CONTROLLER_SECRET: runtimeControllerSecret,
        AGENT_PLATFORM_LOCAL_CREDENTIALS_REPOSITORY_ROOT: repositoryRoot,
        AGENT_PLATFORM_RUNTIME_LEASE_SECONDS: '3',
        AGENT_PLATFORM_RUNTIME_HEARTBEAT_SECONDS: '1',
      },
      url: 'http://127.0.0.1:18092/health',
      reuseExistingServer: false,
      timeout: 120_000,
    },
    {
      command: 'pnpm dev --host 127.0.0.1 --port 15175',
      cwd: frontendRoot,
      env: { VITE_API_PROXY_TARGET: 'http://127.0.0.1:18002' },
      url: 'http://127.0.0.1:15175',
      reuseExistingServer: false,
      timeout: 120_000,
    },
  ],
})
