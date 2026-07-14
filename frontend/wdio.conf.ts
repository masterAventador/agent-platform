import { join } from 'node:path'

const binaryName = process.platform === 'win32' ? 'agent-platform-desktop.exe' : 'agent-platform-desktop'
const appBinaryPath = join(process.cwd(), 'src-tauri', 'target', 'debug', binaryName)

function stripManagedContentLength(requestOptions: RequestInit): RequestInit {
  const headers = new Headers(requestOptions.headers)
  headers.delete('content-length')
  return { ...requestOptions, headers }
}

export const config = {
  runner: 'local',
  specs: ['./e2e-tauri/**/*.spec.ts'],
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
    timeout: 60_000,
  },
}
