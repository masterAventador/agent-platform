import { defineConfig } from '@playwright/test'

export default defineConfig({
  testDir: './e2e',
  testMatch: 'mvp-profile.spec.ts',
  fullyParallel: false,
  workers: 1,
  retries: 0,
  reporter: 'list',
  timeout: 180_000,
  expect: { timeout: 120_000 },
  use: {
    baseURL: process.env.PLAYWRIGHT_MVP_BASE_URL,
    channel: 'chrome',
    trace: 'off',
  },
})
