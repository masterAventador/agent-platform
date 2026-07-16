import { defineConfig } from '@playwright/test'

import baseConfig from './playwright.config'

const webServer = Array.isArray(baseConfig.webServer)
  ? baseConfig.webServer.map((server, index) => index === 1
    ? {
        ...server,
        command: server.command.replace(
          'agent_platform.api.app:app',
          'tests.fixtures.video_studio_e2e:app',
        ),
      }
    : server)
  : baseConfig.webServer

export default defineConfig({
  ...baseConfig,
  testMatch: 'video-studio-media-library.spec.ts',
  fullyParallel: false,
  workers: 1,
  webServer,
})
