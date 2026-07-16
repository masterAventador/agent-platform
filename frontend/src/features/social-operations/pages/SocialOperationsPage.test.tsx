import { fireEvent, render, screen, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import type {
  PlatformAdapter,
  SocialLoginSignal,
  SocialLoginState,
  SocialOperationsBridge,
} from '../../../platform'
import {
  authorizeSocialAccountAction,
  getSocialAccountGovernance,
  listSocialDevices,
  pauseSocialAccount,
  registerSocialDevice,
  remoteStopSocialAccount,
  resumeSocialAccount,
} from '../api/device-accounts'
import { SocialOperationsPage } from './SocialOperationsPage'

vi.mock('../api/device-accounts', () => ({
  authorizeSocialAccountAction: vi.fn(),
  getSocialAccountGovernance: vi.fn(),
  listSocialDevices: vi.fn(),
  pauseSocialAccount: vi.fn(),
  registerSocialDevice: vi.fn(),
  remoteStopSocialAccount: vi.fn(),
  resumeSocialAccount: vi.fn(),
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

const healthyGovernance = {
  account_id: accountId,
  status: 'healthy' as const,
  circuit_open: false,
  health_score: 100,
  recent_tasks: [],
  failure_trend: {},
  policy_limits: {
    'social.account.health_check': {
      action_type: 'social.account.health_check',
      daily_limit: 10,
      effective_daily_limit: 2,
      remaining_daily: 1,
      min_interval_seconds: 60,
      cold_start_days: 7,
      consecutive_failure_threshold: 3,
      next_available_at: null,
    },
  },
  recommendations: [],
}

function createPlatform(supported = true) {
  let loginState: SocialLoginState = 'logged_out'
  const socialOperations = {
    installSidecar: vi.fn().mockResolvedValue('1.2.3'),
    downloadSidecar: vi.fn().mockResolvedValue('1.2.3'),
    prepareAccount: vi.fn().mockResolvedValue({
      state: 'logged_out',
      circuit_open: true,
      session_revision: 0,
    }),
    signalLogin: vi.fn(async (_accountId: string, signal: SocialLoginSignal) => {
      if (signal === 'begin_qr' && loginState === 'logged_out') {
        loginState = 'awaiting_scan'
        return { state: loginState, circuit_open: true, session_revision: 0 }
      }
      if (signal === 'qr_scanned' && loginState === 'awaiting_scan') {
        loginState = 'awaiting_confirmation'
        return { state: loginState, circuit_open: true, session_revision: 0 }
      }
      if (signal === 'authenticated' && loginState === 'awaiting_confirmation') {
        loginState = 'healthy'
        return { state: loginState, circuit_open: false, session_revision: 0 }
      }
      if (signal === 'captcha_required' || signal === 'risk_control') {
        loginState = 'human_handoff'
        return { state: loginState, circuit_open: true, session_revision: 0 }
      }
      throw new Error('invalid login transition')
    }),
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
    vi.mocked(getSocialAccountGovernance).mockResolvedValue(healthyGovernance)
    vi.mocked(authorizeSocialAccountAction).mockResolvedValue({
      account_id: accountId,
      action_type: 'social.account.health_check',
      allowed: true,
      remaining_daily: 0,
      next_available_at: null,
      idempotency_key: 'health-check',
    })
    vi.mocked(pauseSocialAccount).mockResolvedValue({
      ...healthyGovernance,
      status: 'paused',
      circuit_open: true,
      recommendations: ['账号已暂停，请完成复核后再恢复自动执行。'],
    })
    vi.mocked(resumeSocialAccount).mockResolvedValue(healthyGovernance)
    vi.mocked(remoteStopSocialAccount).mockResolvedValue({
      ...healthyGovernance,
      status: 'human_handoff',
      circuit_open: true,
      health_score: 80,
      recommendations: ['远程停止已生效，请人工检查后再恢复。'],
    })
  })

  it('设备平台入口只暴露后端支持的平台', async () => {
    const user = userEvent.setup()
    const { platform } = createPlatform()
    render(<SocialOperationsPage platform={platform} workspaceId={tenantId} />)

    const [devicePlatformSelect] = screen.getAllByRole('combobox')
    await user.click(devicePlatformSelect)

    expect(screen.getByText('Windows')).toBeInTheDocument()
    expect(screen.queryByText('Linux')).not.toBeInTheDocument()
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
    await user.click(screen.getByRole('button', { name: '确认已完成扫码' }))
    await user.click(screen.getByRole('button', { name: '确认已登录' }))
    await user.click(screen.getByRole('button', { name: '启动本地执行器' }))
    await user.click(screen.getByRole('button', { name: '检查 Cookie' }))
    await user.type(screen.getByLabelText('Cookie 数据'), 'session=demo')
    await user.click(screen.getByRole('button', { name: '加密保存 Cookie' }))
    await user.click(screen.getByRole('button', { name: '执行无副作用健康检查' }))
    await user.click(screen.getByRole('button', { name: '生成安全诊断' }))

    expect(socialOperations.prepareAccount).toHaveBeenCalledWith('douyin', accountId)
    expect(socialOperations.signalLogin).toHaveBeenNthCalledWith(1, accountId, 'begin_qr')
    expect(socialOperations.signalLogin).toHaveBeenNthCalledWith(2, accountId, 'qr_scanned')
    expect(socialOperations.signalLogin).toHaveBeenNthCalledWith(3, accountId, 'authenticated')
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
    expect(authorizeSocialAccountAction).toHaveBeenCalledWith(
      tenantId,
      accountId,
      expect.objectContaining({
        action_type: 'social.account.health_check',
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
  }, 15_000)

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

  it('设备离线时即使账号治理健康也禁用本地动作', async () => {
    const user = userEvent.setup()
    const { platform } = createPlatform()
    vi.mocked(listSocialDevices).mockResolvedValueOnce([{
      ...device,
      status: 'offline',
    }])
    render(<SocialOperationsPage platform={platform} workspaceId={tenantId} />)

    await user.type(screen.getByLabelText('设备 ID'), deviceId)
    await user.type(screen.getByLabelText('平台账号 ID'), accountId)
    await user.click(screen.getByRole('button', { name: '刷新治理状态' }))

    expect(await screen.findByText('离线')).toBeInTheDocument()
    expect(await screen.findByText('账号健康度 100')).toBeInTheDocument()
    const healthCheck = screen.getByRole('button', { name: '执行无副作用健康检查' })
    expect(healthCheck).toBeDisabled()

    await user.click(healthCheck)
    expect(authorizeSocialAccountAction).not.toHaveBeenCalled()
  })

  it('展示服务端账号治理状态并在熔断时禁用本地动作', async () => {
    const user = userEvent.setup()
    const { platform } = createPlatform()
    vi.mocked(getSocialAccountGovernance).mockResolvedValueOnce({
      ...healthyGovernance,
      status: 'human_handoff',
      circuit_open: true,
      health_score: 40,
      recent_tasks: [{
        account_id: accountId,
        action_type: 'private_message',
        idempotency_key: 'dm-1',
        result: 'failed',
        occurred_at: '2026-07-15T02:30:00Z',
        consecutive_failures: 3,
      }],
      failure_trend: { private_message: 3 },
      recommendations: ['连续失败已触发熔断，请人工检查平台页面和任务参数。'],
    })
    render(<SocialOperationsPage platform={platform} workspaceId={tenantId} />)

    await user.type(screen.getByLabelText('设备 ID'), deviceId)
    await user.type(screen.getByLabelText('平台账号 ID'), accountId)
    await user.click(screen.getByRole('button', { name: '刷新治理状态' }))

    expect(getSocialAccountGovernance).toHaveBeenCalledWith(tenantId, accountId)
    expect(await screen.findByText('账号健康度 40')).toBeInTheDocument()
    expect(screen.getByText('private_message：失败（连续失败 3 次）')).toBeInTheDocument()
    expect(screen.getByText('private_message：3 次失败')).toBeInTheDocument()
    expect(screen.getByText('连续失败已触发熔断，请人工检查平台页面和任务参数。'))
      .toBeInTheDocument()
    expect(screen.getByRole('button', { name: '执行无副作用健康检查' }))
      .toBeDisabled()
  })

  it('暂停、恢复和远程停止都通过服务端治理 API 而不是本地放宽策略', async () => {
    const user = userEvent.setup()
    const { platform } = createPlatform()
    render(<SocialOperationsPage platform={platform} workspaceId={tenantId} />)

    await user.type(screen.getByLabelText('平台账号 ID'), accountId)
    await user.click(screen.getByRole('button', { name: '暂停账号' }))
    await user.click(screen.getByRole('button', { name: '恢复账号' }))
    await user.click(screen.getByRole('button', { name: '远程停止账号' }))

    expect(pauseSocialAccount).toHaveBeenCalledWith(tenantId, accountId, {
      reason: 'operator_review',
    })
    expect(resumeSocialAccount).toHaveBeenCalledWith(tenantId, accountId)
    expect(remoteStopSocialAccount).toHaveBeenCalledWith(tenantId, accountId, {
      reason: 'remote_stop',
    })
  })
})
