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
const runtimeFrontendPort = process.env.PLAYWRIGHT_RUNTIME_FRONTEND_PORT ?? '15174'
const runtimeApiPort = process.env.PLAYWRIGHT_RUNTIME_API_PORT ?? '18001'
const runtimeSandboxPort = process.env.PLAYWRIGHT_RUNTIME_SANDBOX_PORT ?? '18090'
const runtimeMcpStubPort = process.env.PLAYWRIGHT_RUNTIME_MCP_STUB_PORT ?? '18941'
const runtimeRagflowPort = process.env.PLAYWRIGHT_RUNTIME_RAGFLOW_PORT ?? '29381'
const runtimeBaseUrl = process.env.PLAYWRIGHT_RUNTIME_BASE_URL ?? `http://127.0.0.1:${runtimeFrontendPort}`
const runtimeApiUrl = `http://127.0.0.1:${runtimeApiPort}`
const runtimeSandboxUrl = `http://127.0.0.1:${runtimeSandboxPort}`
const runtimeRagflowUrl = `http://127.0.0.1:${runtimeRagflowPort}`
const runtimeMinioEndpoint = `127.0.0.1:${process.env.PLAYWRIGHT_MINIO_API_PORT ?? process.env.MINIO_API_PORT ?? '9000'}`
const backendEnvironment = {
  AGENT_PLATFORM_DATABASE_URL: runtimeDatabaseUrl,
  AGENT_PLATFORM_REDIS_URL: runtimeRedisUrl,
  AGENT_PLATFORM_RUN_QUEUE_STREAM_NAME: runtimeQueueStream,
  AGENT_PLATFORM_RUN_QUEUE_GROUP_NAME: runtimeQueueGroup,
  AGENT_PLATFORM_LLM_GATEWAY_ALLOWED_ALIASES: '["general-purpose","slow-cancel","structured-output","tool-call","slow-complete"]',
  AGENT_PLATFORM_RAGFLOW_URL: runtimeRagflowUrl,
  AGENT_PLATFORM_RAGFLOW_API_KEY: 'ragflow-runtime-e2e-key',
}

export default defineConfig({
  testDir: './e2e',
  testMatch: ['runtime.spec.ts', 'knowledge-runtime.spec.ts'],
  globalSetup: './e2e/runtime-global-setup.ts',
  globalTeardown: './e2e/runtime-global-teardown.ts',
  fullyParallel: false,
  workers: 1,
  retries: 0,
  reporter: 'list',
  timeout: 180_000,
  expect: { timeout: 120_000 },
  use: {
    baseURL: runtimeBaseUrl,
    channel: 'chrome',
    trace: 'retain-on-failure',
  },
  webServer: [
    {
      command: `uv run uvicorn tests.fixtures.mcp_stub:app --host 127.0.0.1 --port ${runtimeMcpStubPort}`,
      cwd: `${repositoryRoot}/backend`,
      url: `http://127.0.0.1:${runtimeMcpStubPort}/health`,
      command: `uv run uvicorn tests.fixtures.ragflow_stub:app --host 127.0.0.1 --port ${runtimeRagflowPort}`,
      cwd: `${repositoryRoot}/backend`,
      url: `${runtimeRagflowUrl}/health`,
      reuseExistingServer: false,
      timeout: 120_000,
    },
    {
      command: `uv run uvicorn agent_platform.sandbox.controller.main:app --host 127.0.0.1 --port ${runtimeSandboxPort}`,
      cwd: `${repositoryRoot}/backend`,
      env: {
        SANDBOX_CONTROLLER_BEARER_SECRET: runtimeControllerSecret,
        SANDBOX_CONTROLLER_IMAGE: sandboxImage,
      },
      url: `${runtimeSandboxUrl}/health/ready`,
      reuseExistingServer: false,
      timeout: 120_000,
    },
    {
      command: `uv run uvicorn agent_platform.api.app:app --host 127.0.0.1 --port ${runtimeApiPort}`,
      cwd: `${repositoryRoot}/backend`,
      env: {
        ...backendEnvironment,
        AGENT_PLATFORM_AUTH_REGISTER_LIMIT_PER_MINUTE: '100',
        AGENT_PLATFORM_AUTH_LOGIN_LIMIT_PER_MINUTE: '100',
      },
      url: `${runtimeApiUrl}/api/v1/health/live`,
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
        AGENT_PLATFORM_MINIO_ENDPOINT: runtimeMinioEndpoint,
        AGENT_PLATFORM_MINIO_ACCESS_KEY: 'agent_platform',
        AGENT_PLATFORM_MINIO_SECRET_KEY: 'agent-platform-local-minio',
        AGENT_PLATFORM_SANDBOX_CONTROLLER_URL: runtimeSandboxUrl,
        AGENT_PLATFORM_SANDBOX_CONTROLLER_SECRET: runtimeControllerSecret,
        AGENT_PLATFORM_LOCAL_CREDENTIALS_REPOSITORY_ROOT: repositoryRoot,
      },
      reuseExistingServer: false,
      timeout: 120_000,
    },
    {
      command: `pnpm dev --host 127.0.0.1 --port ${runtimeFrontendPort}`,
      cwd: frontendRoot,
      env: { VITE_API_PROXY_TARGET: runtimeApiUrl },
      url: runtimeBaseUrl,
      reuseExistingServer: false,
      timeout: 120_000,
    },
  ],
})
