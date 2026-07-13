import { defineConfig } from '@playwright/test'

import baseConfig from './playwright.config'


export default defineConfig({
  ...baseConfig,
  testMatch: ['demo-seed.spec.ts', 'rbac.demo-seed.spec.ts'],
  testIgnore: [],
  globalSetup: './e2e/demo-seed-global-setup.ts',
  globalTeardown: './e2e/global-teardown.ts',
  fullyParallel: false,
  workers: 1,
})
