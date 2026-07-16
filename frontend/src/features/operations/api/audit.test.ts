import { beforeEach, describe, expect, it, vi } from 'vitest'

import { apiClient } from '../../../api/client'
import { listAuditEvents } from './audit'


vi.mock('../../../api/client', () => ({
  apiClient: { get: vi.fn() },
}))

const tenantId = '10000000-0000-4000-8000-000000000010'
const actorId = '20000000-0000-4000-8000-000000000020'
const eventId = '30000000-0000-4000-8000-000000000030'

describe('audit API', () => {
  beforeEach(() => vi.clearAllMocks())

  it('按租户读取审计事件并校验安全字段', async () => {
    vi.mocked(apiClient.get).mockResolvedValue({
      data: [{
        id: eventId,
        tenant_id: tenantId,
        actor_user_id: actorId,
        sequence: 2,
        action: 'employee.created',
        resource_type: 'employee',
        resource_id: '40000000-0000-4000-8000-000000000040',
        outcome: 'succeeded',
        correlation_id: 'run-correlation-1',
        previous_hash: 'a'.repeat(64),
        event_hash: 'b'.repeat(64),
        metadata: { runtime_type: 'flow' },
        occurred_at: '2026-07-16T08:00:00Z',
      }],
    })

    await expect(listAuditEvents(tenantId)).resolves.toMatchObject([
      {
        id: eventId,
        actor_user_id: actorId,
        sequence: 3,
        action: 'employee.created',
        metadata: { runtime_type: 'flow' },
      },
    ])
    expect(apiClient.get).toHaveBeenCalledWith('/audit/events', {
      headers: { 'X-Tenant-ID': tenantId },
      params: { limit: 100 },
    })
  })

  it('拒绝协议外字段，避免前端误展示未脱敏内容', async () => {
    vi.mocked(apiClient.get).mockResolvedValue({
      data: [{
        id: eventId,
        tenant_id: tenantId,
        actor_user_id: actorId,
        action: 'run.created',
        resource_type: 'run',
        resource_id: null,
        outcome: 'succeeded',
        correlation_id: null,
        previous_hash: 'b'.repeat(64),
        event_hash: 'c'.repeat(64),
        metadata: { input: 'password=must-not-enter-client' },
        raw_body: 'must-not-enter-client',
        occurred_at: '2026-07-16T08:00:00Z',
      }],
    })

    await expect(listAuditEvents(tenantId)).rejects.toBeDefined()
  })
})
