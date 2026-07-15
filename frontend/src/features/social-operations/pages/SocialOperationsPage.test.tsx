import { fireEvent, render, screen, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import type { PlatformAdapter, SocialOperationsBridge } from '../../../platform'
import { listSocialDevices, registerSocialDevice } from '../api/device-accounts'
import { SocialOperationsPage } from './SocialOperationsPage'

vi.mock('../api/device-accounts', () => ({
  listSocialDevices: vi.fn(),
  registerSocialDevice: vi.fn(),
}))

const tenantId = '00000000-0000-4000-8000-000000000101'
const deviceId = '00000000-0000-4000-8000-000000000301'
const accountId = '00000000-0000-4000-8000-000000000501'

const device = {
  device_id: deviceId,
  tenant_id: tenantId,
  owner_user_id: '00000000-0000-4000-8000-000000000201',
  display_name: 'Marketing Mac',
  platform: 'macos' as const,
  app_version: '0.1.0',
  executor_version: '1.0.0',
  status: 'online' as const,
  registered_at: '2026-07-15T02:00:00Z',
  last_seen_at: '2026-07-15T02:00:00Z',
  heartbeat_sequence: 0,
}

function createPlatform(supported = true) {
  const snapshot = {
    state: 'healthy' as const,
    circuit_open: false,
    session_revision: 2,
  }
  const socialOperations = {
    installSidecar: vi.fn().mockResolvedValue('1.2.3'),
    downloadSidecar: vi.fn().mockResolvedValue('1.2.3'),
    prepareAccount: vi.fn().mockResolvedValue({
      state: 'logged_out',
      circuit_open: true,
      session_revision: 0,
    }),
    signalLogin: vi.fn().mockResolvedValue(snapshot),
    storeCookies: vi.fn().mockResolvedValue(undefined),
    hasCookies: vi.fn().mockResolvedValue(true),
    startAccount: vi.fn().mockResolvedValue({
      running: true,
      protocolVersion: '1.0',
      capabilityId: 'social-operations',
    }),
    invokeAccount: vi.fn().mockResolvedValue({ status: 'accepted' }),
    logoutAccount: vi.fn().mockResolvedValue(undefined),
    emergencyStop: vi.fn().mockResolvedValue(undefined),
    takeSafeDiagnostics: vi.fn().mockResolvedValue(['cookie=[REDACTED]']),
  } satisfies SocialOperationsBridge
  const platform = {
    capabilities: () => ({ socialOperations: supported }),
    socialOperations,
    selectFile: vi.fn().mockResolvedValue({
      name: 'social-sidecar.zip',
      bytes: new Uint8Array([1, 2, 3]),
    }),
  } as unknown as PlatformAdapter
  return { platform, socialOperations }
}

describe('B02 设备与平台账号中心', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    vi.mocked(listSocialDevices).mockResolvedValue([])
    vi.mocked(registerSocialDevice).mockResolvedValue(device)
  })

  it('通过生产页面注册设备并调用 B02 类型化账号运行时', async () => {
    const user = userEvent.setup()
    const { platform, socialOperations } = createPlatform()
    render(<SocialOperationsPage platform={platform} workspaceId={tenantId} />)

    await user.type(screen.getByLabelText('设备 ID'), deviceId)
    await user.type(screen.getByLabelText('设备名称'), 'Marketing Mac')
    await user.click(screen.getByRole('button', { name: '注册本机设备' }))
    expect(registerSocialDevice).toHaveBeenCalledWith(tenantId, expect.objectContaining({
      device_id: deviceId,
      display_name: 'Marketing Mac',
    }))
    expect(await screen.findByText('在线')).toBeInTheDocument()

    await user.type(screen.getByLabelText('平台账号 ID'), accountId)
    await user.click(screen.getByRole('button', { name: '准备账号环境' }))
    await user.click(screen.getByRole('button', { name: '开始扫码' }))
    await user.click(screen.getByRole('button', { name: '确认已登录' }))
    await user.click(screen.getByRole('button', { name: '启动本地执行器' }))
    await user.click(screen.getByRole('button', { name: '检查 Cookie' }))
    await user.type(screen.getByLabelText('Cookie 数据'), 'session=demo')
    await user.click(screen.getByRole('button', { name: '加密保存 Cookie' }))
    await user.click(screen.getByRole('button', { name: '执行无副作用健康检查' }))
    await user.click(screen.getByRole('button', { name: '生成安全诊断' }))

    expect(socialOperations.prepareAccount).toHaveBeenCalledWith('douyin', accountId)
    expect(socialOperations.signalLogin).toHaveBeenNthCalledWith(1, accountId, 'begin_qr')
    expect(socialOperations.signalLogin).toHaveBeenNthCalledWith(2, accountId, 'authenticated')
    expect(socialOperations.startAccount).toHaveBeenCalledWith(accountId)
    expect(socialOperations.hasCookies).toHaveBeenCalledWith(accountId)
    expect(socialOperations.storeCookies).toHaveBeenCalledWith(
      accountId,
      new TextEncoder().encode('session=demo'),
    )
    expect(socialOperations.invokeAccount).toHaveBeenCalledWith(
      accountId,
      expect.objectContaining({
        protocol_version: '1.0',
        message_type: 'task.request',
        task_type: 'social.account.health_check',
      }),
    )
    expect(socialOperations.takeSafeDiagnostics).toHaveBeenCalledOnce()
    expect(await screen.findByText('cookie=[REDACTED]')).toBeInTheDocument()

    await user.click(screen.getByRole('button', { name: '上报验证码' }))
    await user.click(screen.getByRole('button', { name: '紧急停止' }))
    await user.click(screen.getByRole('button', { name: '注销账号' }))
    expect(socialOperations.signalLogin).toHaveBeenLastCalledWith(
      accountId,
      'captcha_required',
    )
    expect(socialOperations.emergencyStop).toHaveBeenCalledWith(accountId)
    expect(socialOperations.logoutAccount).toHaveBeenCalledWith(accountId)
  })

  it('使用签名 Manifest 下载或本地安装 Sidecar', async () => {
    const user = userEvent.setup()
    const { platform, socialOperations } = createPlatform()
    render(<SocialOperationsPage platform={platform} workspaceId={tenantId} />)

    const installer = screen.getByRole('region', { name: '执行器安装' })
    fireEvent.change(within(installer).getByLabelText('Manifest JSON'), { target: { value: JSON.stringify({
      version: '1.2.3',
      platform: 'macos',
      arch: 'aarch64',
      sha256: 'a'.repeat(64),
      package_size: 3,
    }) } })
    await user.type(within(installer).getByLabelText('签名 Base64'), 'BAU=')
    await user.type(
      within(installer).getByLabelText('安全下载地址'),
      'https://updates.example.com/social-sidecar',
    )
    await user.click(within(installer).getByRole('button', { name: '下载并安装' }))
    await user.click(within(installer).getByRole('button', { name: '选择本地安装包' }))
    await user.click(within(installer).getByRole('button', { name: '验证并安装本地包' }))

    expect(socialOperations.downloadSidecar).toHaveBeenCalledWith(expect.objectContaining({
      downloadUrl: 'https://updates.example.com/social-sidecar',
    }))
    expect(platform.selectFile).toHaveBeenCalled()
    expect(socialOperations.installSidecar).toHaveBeenCalledWith(expect.objectContaining({
      package: new Uint8Array([1, 2, 3]),
    }))
  })

  it('Web 不支持时明确告知且不暴露可误触的本地操作', () => {
    const { platform } = createPlatform(false)
    render(<SocialOperationsPage platform={platform} workspaceId={tenantId} />)

    expect(screen.getByText('当前 Web 环境不支持本地执行设备')).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: '准备账号环境' })).not.toBeInTheDocument()
  })

  it('失败时显示受控错误而不泄露原生异常', async () => {
    const user = userEvent.setup()
    const { platform, socialOperations } = createPlatform()
    socialOperations.prepareAccount.mockRejectedValue(
      new Error('token=secret /Users/demo/private-profile'),
    )
    render(<SocialOperationsPage platform={platform} workspaceId={tenantId} />)

    await user.type(screen.getByLabelText('平台账号 ID'), accountId)
    await user.click(screen.getByRole('button', { name: '准备账号环境' }))

    expect(await screen.findByText('账号操作失败，请检查执行器、登录态和网络后重试。'))
      .toBeInTheDocument()
    expect(screen.queryByText(/token=secret/)).not.toBeInTheDocument()
  })
})
