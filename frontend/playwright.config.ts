import { defineConfig } from '@playwright/test'

const webPort = process.env.PLAYWRIGHT_WEB_PORT ?? '15173'
const apiPort = process.env.PLAYWRIGHT_API_PORT ?? '18000'
const ragflowPort = process.env.PLAYWRIGHT_RAGFLOW_PORT ?? '29380'
const postgresPort = process.env.PLAYWRIGHT_POSTGRES_PORT ?? '5432'
const redisPort = process.env.PLAYWRIGHT_REDIS_PORT ?? '6379'

export default defineConfig({
  testDir: './e2e',
  testIgnore: [
    'demo-seed.spec.ts',
    'mvp-profile.spec.ts',
    'rbac.demo-seed.spec.ts',
    'runtime.spec.ts',
    'runtime-recovery.spec.ts',
  ],
  globalSetup: './e2e/global-setup.ts',
  globalTeardown: './e2e/global-teardown.ts',
  fullyParallel: true,
  retries: 0,
  reporter: 'list',
  use: {
    baseURL: `http://127.0.0.1:${webPort}`,
    channel: 'chrome',
    trace: 'retain-on-failure',
  },
  webServer: [
    {
      command: `uv run uvicorn tests.fixtures.ragflow_stub:app --host 127.0.0.1 --port ${ragflowPort}`,
      cwd: '../backend',
      url: `http://127.0.0.1:${ragflowPort}/health`,
      reuseExistingServer: false,
    },
    {
      command: `uv run uvicorn agent_platform.api.app:app --host 127.0.0.1 --port ${apiPort}`,
      cwd: '../backend',
      env: {
        AGENT_PLATFORM_DATABASE_URL:
          `postgresql+asyncpg://agent_platform:agent-platform-local-postgres@127.0.0.1:${postgresPort}/agent_platform_e2e`,
        AGENT_PLATFORM_REDIS_URL:
          `redis://:agent-platform-local-redis@127.0.0.1:${redisPort}/2`,
        AGENT_PLATFORM_RAGFLOW_URL: `http://127.0.0.1:${ragflowPort}`,
        AGENT_PLATFORM_RAGFLOW_API_KEY: 'ragflow-e2e-key',
        AGENT_PLATFORM_AUTH_REGISTER_LIMIT_PER_MINUTE: '100',
        AGENT_PLATFORM_AUTH_LOGIN_LIMIT_PER_MINUTE: '100',
      },
      url: `http://127.0.0.1:${apiPort}/api/v1/health/live`,
      reuseExistingServer: false,
    },
    {
      command: `pnpm dev --host 127.0.0.1 --port ${webPort}`,
      cwd: '.',
      env: { VITE_API_PROXY_TARGET: `http://127.0.0.1:${apiPort}` },
      url: `http://127.0.0.1:${webPort}`,
      reuseExistingServer: false,
    },
  ],
})
