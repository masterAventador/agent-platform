import { beforeEach, describe, expect, it, vi } from 'vitest'

import { apiClient } from '../../../api/client'
import {
  authorizeSocialAccountAction,
  getSocialAccountGovernance,
  listSocialDevices,
  registerSocialDevice,
} from './device-accounts'

vi.mock('../../../api/client', () => ({
  apiClient: {
    get: vi.fn(),
    post: vi.fn(),
  },
}))

const tenantId = '00000000-0000-4000-8000-000000000101'
const device = {
  device_id: '00000000-0000-4000-8000-000000000301',
  tenant_id: tenantId,
  owner_user_id: '00000000-0000-4000-8000-000000000201',
  display_name: 'Marketing Mac',
  platform: 'macos',
  app_version: '0.1.0',
  executor_version: '1.0.0',
  status: 'online',
  registered_at: '2026-07-15T02:00:00Z',
  last_seen_at: '2026-07-15T02:00:00Z',
  heartbeat_sequence: 0,
}

const governanceSnapshot = {
  account_id: '00000000-0000-4000-8000-000000000501',
  status: 'healthy',
  circuit_open: false,
  health_score: 100,
  recent_tasks: [],
  failure_trend: {},
  policy_limits: {
    publish_video: {
      action_type: 'publish_video',
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

describe('B02 设备 API', () => {
  beforeEach(() => vi.clearAllMocks())

  it('使用租户上下文注册设备并校验返回契约', async () => {
    vi.mocked(apiClient.post).mockResolvedValue({ data: device })

    await expect(registerSocialDevice(tenantId, {
      device_id: device.device_id,
      display_name: device.display_name,
      platform: 'macos',
      app_version: '0.1.0',
      executor_version: '1.0.0',
    })).resolves.toEqual(device)

    expect(apiClient.post).toHaveBeenCalledWith(
      '/social-operations/devices/register',
      expect.objectContaining({ device_id: device.device_id }),
      expect.objectContaining({ headers: { 'X-Tenant-ID': tenantId } }),
    )
  })

  it('列出当前用户可见设备并拒绝畸形响应', async () => {
    vi.mocked(apiClient.get).mockResolvedValueOnce({ data: [device] })
    await expect(listSocialDevices(tenantId)).resolves.toEqual([device])

    vi.mocked(apiClient.get).mockResolvedValueOnce({ data: [{ ...device, status: 'unknown' }] })
    await expect(listSocialDevices(tenantId)).rejects.toThrow()

    vi.mocked(apiClient.get).mockResolvedValueOnce({ data: [{ ...device, platform: 'linux' }] })
    await expect(listSocialDevices(tenantId)).rejects.toThrow()
  })

  it('读取账号治理快照并拒绝未知状态', async () => {
    vi.mocked(apiClient.get).mockResolvedValueOnce({ data: governanceSnapshot })
    await expect(getSocialAccountGovernance(
      tenantId,
      governanceSnapshot.account_id,
    )).resolves.toEqual(governanceSnapshot)
    expect(apiClient.get).toHaveBeenCalledWith(
      `/social-operations/accounts/${governanceSnapshot.account_id}/governance`,
      expect.objectContaining({ headers: { 'X-Tenant-ID': tenantId } }),
    )

    vi.mocked(apiClient.get).mockResolvedValueOnce({
      data: { ...governanceSnapshot, status: 'unknown' },
    })
    await expect(getSocialAccountGovernance(
      tenantId,
      governanceSnapshot.account_id,
    )).rejects.toThrow()
  })

  it('动作执行前只向服务端提交动作类型和幂等键，不允许传本地限额覆盖', async () => {
    vi.mocked(apiClient.post).mockResolvedValue({
      data: {
        account_id: governanceSnapshot.account_id,
        action_type: 'publish_video',
        allowed: true,
        remaining_daily: 0,
        next_available_at: null,
        idempotency_key: 'publish-1',
      },
    })

    await expect(authorizeSocialAccountAction(
      tenantId,
      governanceSnapshot.account_id,
      {
        action_type: 'publish_video',
        idempotency_key: 'publish-1',
      },
    )).resolves.toMatchObject({
      allowed: true,
      remaining_daily: 0,
    })

    expect(apiClient.post).toHaveBeenCalledWith(
      `/social-operations/accounts/${governanceSnapshot.account_id}/actions/authorize`,
      {
        action_type: 'publish_video',
        idempotency_key: 'publish-1',
      },
      expect.objectContaining({ headers: { 'X-Tenant-ID': tenantId } }),
    )
    expect(vi.mocked(apiClient.post).mock.calls[0][1]).not.toHaveProperty(
      'daily_limit',
    )
  })
})
