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
    })
    await expect(adapter.credentials.get('worker-key')).rejects.toEqual(
      expect.objectContaining<Partial<PlatformCapabilityError>>({
        code: 'capability_unavailable',
        capability: 'secureCredentials',
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
})
