import { defineConfig } from '@playwright/test'

export default defineConfig({
  testDir: './e2e',
  globalSetup: './e2e/global-setup.ts',
  globalTeardown: './e2e/global-teardown.ts',
  fullyParallel: true,
  retries: 0,
  reporter: 'list',
  use: {
    baseURL: 'http://127.0.0.1:5173',
    channel: 'chrome',
    trace: 'retain-on-failure',
  },
  webServer: [
    {
      command: 'uv run uvicorn agent_platform.api.app:app --host 127.0.0.1 --port 8000',
      cwd: '../backend',
      env: {
        AGENT_PLATFORM_DATABASE_URL:
          'postgresql+asyncpg://agent_platform:agent-platform-local-postgres@127.0.0.1:5432/agent_platform_e2e',
        AGENT_PLATFORM_REDIS_URL:
          'redis://:agent-platform-local-redis@127.0.0.1:6379/2',
      },
      url: 'http://127.0.0.1:8000/api/v1/health/live',
      reuseExistingServer: false,
    },
    {
      command: 'pnpm dev --host 127.0.0.1 --port 5173',
      cwd: '.',
      url: 'http://127.0.0.1:5173',
      reuseExistingServer: false,
    },
  ],
})
