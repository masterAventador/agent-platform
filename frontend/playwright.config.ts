import { tmpdir } from 'node:os'
import { join } from 'node:path'

import { defineConfig } from '@playwright/test'

import {
  composeEnvironment,
  minioApiPort,
  postgresDatabaseUrl,
  redisDatabaseUrl,
} from './e2e/helpers/compose-core'

const webPort = process.env.PLAYWRIGHT_WEB_PORT ?? process.env.PLATFORM_WEB_PORT ?? '15173'
const apiPort = process.env.PLAYWRIGHT_API_PORT ?? process.env.PLATFORM_API_PORT ?? '18000'
const ragflowPort = process.env.PLAYWRIGHT_RAGFLOW_PORT ?? process.env.RAGFLOW_PORT ?? '29380'
const mcpStubPort = process.env.PLAYWRIGHT_MCP_STUB_PORT ?? '18940'
const credentialsFile = process.env.PLAYWRIGHT_CREDENTIALS_FILE
  ?? join(tmpdir(), `agent-platform-c09-e2e-credentials-${apiPort}.json`)

export default defineConfig({
  testDir: './e2e',
  testIgnore: [
    'demo-seed.spec.ts',
    'mvp-profile.spec.ts',
    'rbac.demo-seed.spec.ts',
    'runtime.spec.ts',
    'runtime-recovery.spec.ts',
    'approvals.spec.ts',
    'knowledge-runtime.spec.ts',
    'memory-runtime.spec.ts',
    'workflow-runtime.spec.ts',
    // 需要 AGENT_PLATFORM_INSTALLED_CAPABILITIES 含 video-studio，只能由
    // playwright.video-studio.config.ts 拉起；默认套件缺这项环境必然失败。
    'video-studio-media-library.spec.ts',
  ],
  globalSetup: './e2e/global-setup.ts',
  globalTeardown: './e2e/global-teardown.ts',
  fullyParallel: true,
  // 多 worker 缩容路径存在 worker 进程退出挂起（300s 后被 force-kill，
  // 套件状态被标 failed；单 worker 全量 2.6 分钟干净退出）。定位到具体
  // 句柄泄漏前固定单 worker，保证套件状态位可信。
  workers: 1,
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
      command: `uv run uvicorn tests.fixtures.mcp_stub:app --host 127.0.0.1 --port ${mcpStubPort}`,
      cwd: '../backend',
      url: `http://127.0.0.1:${mcpStubPort}/health`,
      reuseExistingServer: false,
    },
    {
      command: `uv run uvicorn agent_platform.api.app:app --host 127.0.0.1 --port ${apiPort}`,
      cwd: '../backend',
      env: {
        ...composeEnvironment,
        AGENT_PLATFORM_DATABASE_URL: postgresDatabaseUrl('agent_platform_e2e'),
        AGENT_PLATFORM_REDIS_URL: redisDatabaseUrl(2),
        AGENT_PLATFORM_RAGFLOW_URL: `http://127.0.0.1:${ragflowPort}`,
        AGENT_PLATFORM_RAGFLOW_API_KEY: 'ragflow-e2e-key',
        AGENT_PLATFORM_MINIO_ENDPOINT: `127.0.0.1:${minioApiPort}`,
        AGENT_PLATFORM_AUTH_REGISTER_LIMIT_PER_MINUTE: '100',
        AGENT_PLATFORM_AUTH_LOGIN_LIMIT_PER_MINUTE: '100',
        AGENT_PLATFORM_LOCAL_CREDENTIALS_FILE: credentialsFile,
        AGENT_PLATFORM_MCP_CONNECTION_TIMEOUT_SECONDS: '3',
        // C12：调度循环由 scheduled-tasks.spec.ts 用独立进程承载，才能真实
        // SIGKILL/重启验证恢复、并起两个副本验证不重复触发。这个 API 只服务
        // 界面；若它也跑调度器，会与测试进程竞争同一批任务，断言不再确定。
        AGENT_PLATFORM_SCHEDULER_ENABLED: 'false',
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
