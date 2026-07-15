import { join } from 'node:path'

const binaryName = process.platform === 'win32' ? 'agent-platform-desktop.exe' : 'agent-platform-desktop'
const appBinaryPath = join(process.cwd(), 'src-tauri', 'target', 'debug', binaryName)
const mvpWebUrl = process.env.TAURI_MVP_WEB_URL
const desktopApiBaseUrl = mvpWebUrl
  ? new URL('/api/v1', mvpWebUrl).toString()
  : 'http://127.0.0.1:18000/api/v1'
process.env.AGENT_PLATFORM_DESKTOP_API_BASE_URL ??= desktopApiBaseUrl
if (mvpWebUrl === undefined) {
  delete process.env.AGENT_PLATFORM_DESKTOP_WEB_URL
} else {
  process.env.AGENT_PLATFORM_DESKTOP_WEB_URL ??= mvpWebUrl
}
const specs = mvpWebUrl
  ? ['./e2e-tauri/mvp-profile.spec.ts']
  : ['./e2e-tauri/app.spec.ts']

function stripManagedContentLength(requestOptions: RequestInit): RequestInit {
  const headers = new Headers(requestOptions.headers)
  headers.delete('content-length')
  return { ...requestOptions, headers }
}

export const config = {
  runner: 'local',
  specs,
  maxInstances: 1,
  services: [
    [
      '@wdio/tauri-service',
      {
        appBinaryPath,
        driverProvider: 'embedded',
        captureBackendLogs: true,
        captureFrontendLogs: true,
        startTimeout: 60_000,
      },
    ],
  ],
  capabilities: [
    {
      browserName: 'tauri',
      'tauri:options': {
        application: appBinaryPath,
      },
    },
  ],
  logLevel: 'warn',
  // 交给 HTTP 客户端按最终请求体计算，兼容受管开发环境中的 dispatcher。
  transformRequest: stripManagedContentLength,
  waitforTimeout: 15_000,
  connectionRetryTimeout: 90_000,
  connectionRetryCount: 1,
  framework: 'mocha',
  reporters: ['spec'],
  mochaOpts: {
    ui: 'bdd',
    // The MVP desktop flow includes a worker-backed completion wait of up to
    // two minutes, so the enclosing test must outlive that inner deadline.
    timeout: 180_000,
  },
}
