import { beforeEach, describe, expect, it, vi } from 'vitest'

import { apiClient } from '../../../api/client'
import {
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
  })
})
