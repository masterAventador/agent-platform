import { defineConfig } from '@playwright/test'


export default defineConfig({
  testDir: './e2e',
  testMatch: 'deployment-smoke.spec.ts',
  fullyParallel: false,
  retries: 0,
  reporter: 'list',
  use: {
    baseURL: process.env.PLATFORM_E2E_BASE_URL ?? 'http://127.0.0.1:8080',
    channel: 'chrome',
    trace: 'retain-on-failure',
  },
})
