import { defineConfig } from '@playwright/test'

import {
  frontendRoot,
  repositoryRoot,
  runtimeControllerSecret,
  runtimeDatabaseUrl,
  runtimeQueueGroup,
  runtimeQueueStream,
  runtimeRedisUrl,
} from './e2e/helpers/runtime-infra'


const sandboxImage = process.env.SANDBOX_CONTROLLER_IMAGE
  ?? 'python:3.12.13-slim-bookworm@sha256:8a7e7cc04fd3e2bd787f7f24e22d5d119aa590d429b50c95dfe12b3abe52f48b'
const backendEnvironment = {
  AGENT_PLATFORM_DATABASE_URL: runtimeDatabaseUrl,
  AGENT_PLATFORM_REDIS_URL: runtimeRedisUrl,
  AGENT_PLATFORM_RUN_QUEUE_STREAM_NAME: runtimeQueueStream,
  AGENT_PLATFORM_RUN_QUEUE_GROUP_NAME: runtimeQueueGroup,
}

export default defineConfig({
  testDir: './e2e',
  testMatch: 'runtime.spec.ts',
  globalSetup: './e2e/runtime-global-setup.ts',
  globalTeardown: './e2e/runtime-global-teardown.ts',
  fullyParallel: false,
  workers: 1,
  retries: 0,
  reporter: 'list',
  timeout: 180_000,
  expect: { timeout: 120_000 },
  use: {
    baseURL: process.env.PLAYWRIGHT_RUNTIME_BASE_URL ?? 'http://127.0.0.1:15174',
    channel: 'chrome',
    trace: 'retain-on-failure',
  },
  webServer: [
    {
      command: 'uv run uvicorn agent_platform.sandbox.controller.main:app --host 127.0.0.1 --port 18090',
      cwd: `${repositoryRoot}/backend`,
      env: {
        SANDBOX_CONTROLLER_BEARER_SECRET: runtimeControllerSecret,
        SANDBOX_CONTROLLER_IMAGE: sandboxImage,
      },
      url: 'http://127.0.0.1:18090/health/ready',
      reuseExistingServer: false,
      timeout: 120_000,
    },
    {
      command: 'uv run uvicorn agent_platform.api.app:app --host 127.0.0.1 --port 18001',
      cwd: `${repositoryRoot}/backend`,
      env: {
        ...backendEnvironment,
        AGENT_PLATFORM_AUTH_REGISTER_LIMIT_PER_MINUTE: '100',
        AGENT_PLATFORM_AUTH_LOGIN_LIMIT_PER_MINUTE: '100',
      },
      url: 'http://127.0.0.1:18001/api/v1/health/live',
      reuseExistingServer: false,
      timeout: 120_000,
    },
    {
      command: 'uv run python -m tests.fixtures.runtime_dispatcher',
      cwd: `${repositoryRoot}/backend`,
      env: backendEnvironment,
      reuseExistingServer: false,
      timeout: 120_000,
    },
    {
      command: 'uv run python -m tests.fixtures.runtime_worker',
      cwd: `${repositoryRoot}/backend`,
      env: {
        ...backendEnvironment,
        AGENT_PLATFORM_MINIO_ENDPOINT: '127.0.0.1:9000',
        AGENT_PLATFORM_MINIO_ACCESS_KEY: 'agent_platform',
        AGENT_PLATFORM_MINIO_SECRET_KEY: 'agent-platform-local-minio',
        AGENT_PLATFORM_SANDBOX_CONTROLLER_URL: 'http://127.0.0.1:18090',
        AGENT_PLATFORM_SANDBOX_CONTROLLER_SECRET: runtimeControllerSecret,
        AGENT_PLATFORM_LOCAL_CREDENTIALS_REPOSITORY_ROOT: repositoryRoot,
      },
      reuseExistingServer: false,
      timeout: 120_000,
    },
    {
      command: 'pnpm dev --host 127.0.0.1 --port 15174',
      cwd: frontendRoot,
      env: { VITE_API_PROXY_TARGET: 'http://127.0.0.1:18001' },
      url: 'http://127.0.0.1:15174',
      reuseExistingServer: false,
      timeout: 120_000,
    },
  ],
})
