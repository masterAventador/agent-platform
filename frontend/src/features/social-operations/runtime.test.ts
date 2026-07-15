import { describe, expect, it, vi } from 'vitest'

import type { PlatformAdapter, SocialOperationsBridge } from '../../platform'
import { createSocialOperationsRuntime } from './runtime'

describe('Social Operations 业务运行时入口', () => {
  it('账号执行只使用 B02 类型化账号桥接，不启动 B01 current_exe 测试执行器', async () => {
    const socialOperations = {
      prepareAccount: vi.fn().mockResolvedValue({
        state: 'logged_out',
        circuit_open: true,
        session_revision: 0,
      }),
      startAccount: vi.fn().mockResolvedValue({
        running: true,
        protocolVersion: '1.0',
        capabilityId: 'social-operations',
      }),
      invokeAccount: vi.fn().mockResolvedValue({ status: 'accepted' }),
    } as unknown as SocialOperationsBridge
    const legacyStart = vi.fn(() => {
      throw new Error('不应启动 B01 current_exe 测试执行器')
    })
    const platform = {
      capabilities: () => ({ socialOperations: true }),
      socialOperations,
      localExecutor: { start: legacyStart },
    } as unknown as PlatformAdapter
    const runtime = createSocialOperationsRuntime(platform)
    const account = runtime.account('douyin', 'account-1')
    const request = { protocol_version: '1.0', message_type: 'task.request' }

    await expect(account.prepare()).resolves.toEqual(expect.objectContaining({ state: 'logged_out' }))
    await expect(account.start()).resolves.toEqual(expect.objectContaining({ running: true }))
    await expect(account.invoke(request)).resolves.toEqual({ status: 'accepted' })

    expect(socialOperations.prepareAccount).toHaveBeenCalledWith('douyin', 'account-1')
    expect(socialOperations.startAccount).toHaveBeenCalledWith('account-1')
    expect(socialOperations.invokeAccount).toHaveBeenCalledWith('account-1', request)
    expect(legacyStart).not.toHaveBeenCalled()
  })

  it('把 Sidecar、登录、Cookie、注销、紧停和安全诊断全部交给类型化桥接', async () => {
    const snapshot = {
      state: 'healthy' as const,
      circuit_open: false,
      session_revision: 2,
    }
    const socialOperations = {
      installSidecar: vi.fn().mockResolvedValue('1.2.3'),
      downloadSidecar: vi.fn().mockResolvedValue('1.2.3'),
      prepareAccount: vi.fn().mockResolvedValue(snapshot),
      signalLogin: vi.fn().mockResolvedValue(snapshot),
      storeCookies: vi.fn().mockResolvedValue(undefined),
      hasCookies: vi.fn().mockResolvedValue(true),
      startAccount: vi.fn(),
      invokeAccount: vi.fn(),
      logoutAccount: vi.fn().mockResolvedValue(undefined),
      emergencyStop: vi.fn().mockResolvedValue(undefined),
      takeSafeDiagnostics: vi.fn().mockResolvedValue(['cookie=[REDACTED]']),
    } satisfies SocialOperationsBridge
    const platform = {
      capabilities: () => ({ socialOperations: true }),
      socialOperations,
    } as unknown as PlatformAdapter
    const runtime = createSocialOperationsRuntime(platform)
    const manifest = {
      version: '1.2.3',
      platform: 'macos',
      arch: 'aarch64',
      sha256: 'a'.repeat(64),
      package_size: 3,
    }
    const install = {
      manifest,
      package: new Uint8Array([1, 2, 3]),
      signature: new Uint8Array([4, 5]),
    }
    const download = {
      downloadUrl: 'https://updates.example.com/social-sidecar',
      manifest,
      signature: new Uint8Array([6, 7]),
    }
    const account = runtime.account('douyin', 'account-1')

    await expect(runtime.installSidecar(install)).resolves.toBe('1.2.3')
    await expect(runtime.downloadSidecar(download)).resolves.toBe('1.2.3')
    await expect(account.signalLogin('authenticated')).resolves.toEqual(snapshot)
    await account.storeCookies(new Uint8Array([8, 9]))
    await expect(account.hasCookies()).resolves.toBe(true)
    await account.logout()
    await account.emergencyStop()
    await expect(runtime.takeSafeDiagnostics()).resolves.toEqual(['cookie=[REDACTED]'])

    expect(socialOperations.installSidecar).toHaveBeenCalledWith(install)
    expect(socialOperations.downloadSidecar).toHaveBeenCalledWith(download)
    expect(socialOperations.signalLogin).toHaveBeenCalledWith('account-1', 'authenticated')
    expect(socialOperations.storeCookies).toHaveBeenCalledWith(
      'account-1',
      new Uint8Array([8, 9]),
    )
    expect(socialOperations.hasCookies).toHaveBeenCalledWith('account-1')
    expect(socialOperations.logoutAccount).toHaveBeenCalledWith('account-1')
    expect(socialOperations.emergencyStop).toHaveBeenCalledWith('account-1')
    expect(socialOperations.takeSafeDiagnostics).toHaveBeenCalledOnce()
  })
})
