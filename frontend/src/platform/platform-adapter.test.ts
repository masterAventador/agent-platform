import { beforeEach, describe, expect, it, vi } from 'vitest'

import { createPlatformAdapter } from './index'
import { createTauriPlatformAdapter } from './tauri'
import { PlatformCapabilityError, PlatformOperationError } from './types'
import { createWebPlatformAdapter } from './web'

const tauriMocks = vi.hoisted(() => ({
  invoke: vi.fn(),
  open: vi.fn(),
  save: vi.fn(),
  readFile: vi.fn(),
  writeFile: vi.fn(),
  openUrl: vi.fn(),
  isPermissionGranted: vi.fn(),
  requestPermission: vi.fn(),
  sendNotification: vi.fn(),
}))

vi.mock('@tauri-apps/api/core', () => ({ invoke: tauriMocks.invoke }))
vi.mock('@tauri-apps/plugin-dialog', () => ({
  open: tauriMocks.open,
  save: tauriMocks.save,
}))
vi.mock('@tauri-apps/plugin-fs', () => ({
  readFile: tauriMocks.readFile,
  writeFile: tauriMocks.writeFile,
}))
vi.mock('@tauri-apps/plugin-opener', () => ({ openUrl: tauriMocks.openUrl }))
vi.mock('@tauri-apps/plugin-notification', () => ({
  isPermissionGranted: tauriMocks.isPermissionGranted,
  requestPermission: tauriMocks.requestPermission,
  sendNotification: tauriMocks.sendNotification,
}))

describe('WebPlatformAdapter', () => {
  beforeEach(() => {
    vi.restoreAllMocks()
  })

  it('报告 Web 能力并对安全凭据明确失败', async () => {
    const adapter = createWebPlatformAdapter()

    expect(adapter.capabilities()).toEqual({
      platform: 'web',
      fileSelection: true,
      fileSave: true,
      externalLinks: true,
      notifications: false,
      secureCredentials: false,
      rememberedLogin: false,
      localExecution: false,
      socialOperations: false,
    })
    await expect(adapter.credentials.get('worker-key')).rejects.toEqual(
      expect.objectContaining<Partial<PlatformCapabilityError>>({
        code: 'capability_unavailable',
        capability: 'secureCredentials',
      }),
    )
    await expect(adapter.socialOperations.hasCookies('account-1')).rejects.toEqual(
      expect.objectContaining<Partial<PlatformCapabilityError>>({
        code: 'capability_unavailable',
        capability: 'socialOperations',
      }),
    )
  })

  it('只允许通过新窗口打开 HTTP(S) 外链', async () => {
    const openWindow = vi.spyOn(window, 'open').mockReturnValue({} as Window)
    const adapter = createWebPlatformAdapter()

    await adapter.openExternal('https://example.com/docs')

    expect(openWindow).toHaveBeenCalledWith(
      'https://example.com/docs',
      '_blank',
      'noopener,noreferrer',
    )
    await expect(adapter.openExternal('javascript:alert(1)')).rejects.toEqual(
      expect.objectContaining<Partial<PlatformOperationError>>({ code: 'invalid_input' }),
    )
  })
})

describe('PlatformAdapter 入口', () => {
  it('只在统一入口根据运行环境选择 Web 或 Tauri 实现', () => {
    expect(createPlatformAdapter(false).capabilities().platform).toBe('web')
    expect(createPlatformAdapter(true).capabilities().platform).toBe('tauri')
  })
})

describe('TauriPlatformAdapter', () => {
  beforeEach(() => {
    vi.clearAllMocks()
  })

  it('通过原生对话框选择文件并读取二进制内容', async () => {
    tauriMocks.open.mockResolvedValue('/Users/test/report.pdf')
    tauriMocks.readFile.mockResolvedValue(new Uint8Array([1, 2, 3]))
    const adapter = createTauriPlatformAdapter()

    await expect(adapter.selectFile({ extensions: ['pdf'] })).resolves.toEqual({
      name: 'report.pdf',
      path: '/Users/test/report.pdf',
      bytes: new Uint8Array([1, 2, 3]),
    })
    expect(tauriMocks.open).toHaveBeenCalledWith({
      multiple: false,
      directory: false,
      filters: [{ name: '允许的文件', extensions: ['pdf'] }],
    })
  })

  it('保存、通知、外链和安全凭据全部经过受控原生接口', async () => {
    tauriMocks.save.mockResolvedValue('/Users/test/result.txt')
    tauriMocks.isPermissionGranted.mockResolvedValue(false)
    tauriMocks.requestPermission.mockResolvedValue('granted')
    tauriMocks.invoke.mockResolvedValueOnce(undefined).mockResolvedValueOnce('secret')
    const adapter = createTauriPlatformAdapter()

    await expect(adapter.saveFile({
      suggestedName: 'result.txt',
      bytes: new Uint8Array([4, 5, 6]),
    })).resolves.toEqual({ path: '/Users/test/result.txt' })
    await adapter.openExternal('https://example.com')
    await expect(adapter.notify({ title: '任务完成', body: '成片已生成' })).resolves.toBe(true)
    await adapter.credentials.set('worker-key', 'secret')
    await expect(adapter.credentials.get('worker-key')).resolves.toBe('secret')
    await adapter.credentials.delete('worker-key')

    expect(tauriMocks.writeFile).toHaveBeenCalledWith(
      '/Users/test/result.txt',
      new Uint8Array([4, 5, 6]),
    )
    expect(tauriMocks.openUrl).toHaveBeenCalledWith('https://example.com/')
    expect(tauriMocks.sendNotification).toHaveBeenCalledWith({
      title: '任务完成',
      body: '成片已生成',
    })
    expect(tauriMocks.invoke).toHaveBeenNthCalledWith(1, 'credential_set', {
      key: 'worker-key',
      secret: 'secret',
    })
    expect(tauriMocks.invoke).toHaveBeenNthCalledWith(2, 'credential_get', {
      key: 'worker-key',
    })
    expect(tauriMocks.invoke).toHaveBeenNthCalledWith(3, 'credential_delete', {
      key: 'worker-key',
    })
  })

  it('记住登录只经过 App 私有数据命令', async () => {
    tauriMocks.invoke.mockResolvedValueOnce(undefined).mockResolvedValueOnce('saved-login')
    const adapter = createTauriPlatformAdapter()

    await adapter.rememberedLogin.set('saved-login')
    await expect(adapter.rememberedLogin.get()).resolves.toBe('saved-login')
    await adapter.rememberedLogin.delete()

    expect(tauriMocks.invoke).toHaveBeenNthCalledWith(1, 'remembered_login_set', {
      value: 'saved-login',
    })
    expect(tauriMocks.invoke).toHaveBeenNthCalledWith(2, 'remembered_login_get')
    expect(tauriMocks.invoke).toHaveBeenNthCalledWith(3, 'remembered_login_delete')
  })

  it('从原生命令读取受校验的桌面运行时配置', async () => {
    tauriMocks.invoke.mockResolvedValue({
      apiBaseUrl: 'http://127.0.0.1:18000/api/v1',
      webUrl: null,
    })
    const adapter = createTauriPlatformAdapter()

    await expect(adapter.runtimeConfig()).resolves.toEqual({
      apiBaseUrl: 'http://127.0.0.1:18000/api/v1',
      webUrl: null,
    })
    expect(tauriMocks.invoke).toHaveBeenCalledWith('platform_runtime_config')
  })

  it('只通过原生受认证 IPC 管理 Social Operations Sidecar', async () => {
    tauriMocks.invoke
      .mockResolvedValueOnce({ running: true, protocolVersion: '1.0', capabilityId: 'social-operations' })
      .mockResolvedValueOnce({ ok: true, status: 'accepted' })
      .mockResolvedValueOnce({ running: false, protocolVersion: '1.0', capabilityId: 'social-operations' })
    const adapter = createTauriPlatformAdapter()
    const request = { protocol_version: '1.0', message_type: 'task.request' }

    await expect(adapter.localExecutor.start()).resolves.toEqual(expect.objectContaining({ running: true }))
    await expect(adapter.localExecutor.invoke(request)).resolves.toEqual({ ok: true, status: 'accepted' })
    await expect(adapter.localExecutor.stop()).resolves.toEqual(expect.objectContaining({ running: false }))

    expect(tauriMocks.invoke).toHaveBeenNthCalledWith(1, 'local_executor_start', {
      capabilityId: 'social-operations',
    })
    expect(tauriMocks.invoke).toHaveBeenNthCalledWith(2, 'local_executor_invoke', {
      capabilityId: 'social-operations',
      request,
    })
  })

  it('通过类型化 Social Operations 桥接调用 B02 账号运行时命令', async () => {
    const manifest = {
      version: '1.2.3',
      platform: 'macos',
      arch: 'aarch64',
      sha256: 'a'.repeat(64),
      package_size: 3,
    }
    const snapshot = {
      state: 'healthy',
      circuit_open: false,
      session_revision: 3,
    }
    const status = {
      running: true,
      protocolVersion: '1.0',
      capabilityId: 'social-operations',
    }
    tauriMocks.invoke
      .mockResolvedValueOnce('1.2.3')
      .mockResolvedValueOnce('1.2.3')
      .mockResolvedValueOnce(snapshot)
      .mockResolvedValueOnce(snapshot)
      .mockResolvedValueOnce(undefined)
      .mockResolvedValueOnce(true)
      .mockResolvedValueOnce(status)
      .mockResolvedValueOnce({ status: 'accepted' })
      .mockResolvedValueOnce(undefined)
      .mockResolvedValueOnce(undefined)
      .mockResolvedValueOnce(['token=[REDACTED]'])
    const adapter = createTauriPlatformAdapter()
    const request = { protocol_version: '1.0', message_type: 'task.request' }

    await expect(adapter.socialOperations.installSidecar({
      manifest,
      package: new Uint8Array([1, 2, 3]),
      signature: new Uint8Array([4, 5]),
    })).resolves.toBe('1.2.3')
    await expect(adapter.socialOperations.downloadSidecar({
      downloadUrl: 'https://updates.example.com/social-sidecar',
      manifest,
      signature: new Uint8Array([6, 7]),
    })).resolves.toBe('1.2.3')
    await expect(adapter.socialOperations.prepareAccount('douyin', 'account-1')).resolves.toEqual(snapshot)
    await expect(adapter.socialOperations.signalLogin('account-1', 'login_expired')).resolves.toEqual(snapshot)
    await adapter.socialOperations.storeCookies('account-1', new Uint8Array([8, 9]))
    await expect(adapter.socialOperations.hasCookies('account-1')).resolves.toBe(true)
    await expect(adapter.socialOperations.startAccount('account-1')).resolves.toEqual(status)
    await expect(adapter.socialOperations.invokeAccount('account-1', request)).resolves.toEqual({ status: 'accepted' })
    await adapter.socialOperations.logoutAccount('account-1')
    await adapter.socialOperations.emergencyStop('account-1')
    await expect(adapter.socialOperations.takeSafeDiagnostics()).resolves.toEqual(['token=[REDACTED]'])

    expect(tauriMocks.invoke.mock.calls).toEqual([
      ['social_sidecar_install', {
        manifest,
        package: [1, 2, 3],
        signature: [4, 5],
      }],
      ['social_sidecar_download', {
        downloadUrl: 'https://updates.example.com/social-sidecar',
        manifest,
        signature: [6, 7],
      }],
      ['social_account_prepare', { platform: 'douyin', accountId: 'account-1' }],
      ['social_account_login_signal', { accountId: 'account-1', signal: 'login_expired' }],
      ['social_account_store_cookies', { accountId: 'account-1', cookies: [8, 9] }],
      ['social_account_has_cookies', { accountId: 'account-1' }],
      ['social_account_start', { accountId: 'account-1' }],
      ['social_account_invoke', { accountId: 'account-1', request }],
      ['social_account_logout', { accountId: 'account-1' }],
      ['social_account_emergency_stop', { accountId: 'account-1' }],
      ['social_executor_take_safe_diagnostics'],
    ])
  })
})
