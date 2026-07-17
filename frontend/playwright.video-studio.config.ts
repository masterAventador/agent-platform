import { defineConfig } from '@playwright/test'

import baseConfig from './playwright.config'

const webServer = Array.isArray(baseConfig.webServer)
  ? baseConfig.webServer.map((server) => server.command.includes('agent_platform.api.app:app')
    ? {
        ...server,
        command: server.command.replace(
          'agent_platform.api.app:app',
          'tests.fixtures.video_studio_e2e:app',
        ),
        env: {
          ...server.env,
          // 生产装配：部署安装清单包含 video-studio，路由经 capability gate 挂载。
          AGENT_PLATFORM_INSTALLED_CAPABILITIES: '["social-operations","video-studio"]',
        },
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
