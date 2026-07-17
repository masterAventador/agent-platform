import { beforeEach, describe, expect, it, vi } from 'vitest'

import { apiClient } from '../../../api/client'
import {
  approveApproval,
  getApproval,
  listApprovals,
  rejectApproval,
  transferApproval,
  withdrawApproval,
} from './approvals'


vi.mock('../../../api/client', () => ({
  apiClient: { get: vi.fn(), post: vi.fn() },
}))

const tenantId = '10000000-0000-4000-8000-000000000010'
const approval = {
  id: '20000000-0000-4000-8000-000000000020',
  tenant_id: tenantId,
  source: 'tool_risk',
  approval_type: 'tool.invocation',
  risk_level: 'external',
  status: 'pending',
  requested_by: '30000000-0000-4000-8000-000000000030',
  required_role: 'admin',
  context: { tool_name: 'send_email', arguments: { to: 'a@b.c' } },
  run_id: '40000000-0000-4000-8000-000000000040',
  invocation_id: '50000000-0000-4000-8000-000000000050',
  employee_id: null,
  assignee_id: null,
  decided_by: null,
  reason: null,
  decided_at: null,
  created_at: '2026-07-17T08:00:00Z',
  expires_at: '2026-07-18T08:00:00Z',
  transferred_from_id: null,
  transferred_to_id: null,
  revision: 1,
}

describe('approvals API', () => {
  beforeEach(() => vi.clearAllMocks())

  it('列表返回分页待办并透传视图参数', async () => {
    vi.mocked(apiClient.get).mockResolvedValue({
      data: { items: [approval], total: 1, limit: 50, offset: 0 },
    })

    const result = await listApprovals(tenantId, { view: 'pending' })

    expect(result.total).toBe(1)
    expect(result.items[0].id).toBe(approval.id)
    expect(apiClient.get).toHaveBeenCalledWith('/approvals', expect.objectContaining({
      headers: { 'X-Tenant-ID': tenantId },
      params: expect.objectContaining({ view: 'pending' }),
    }))
  })

  it('详情校验协议字段', async () => {
    vi.mocked(apiClient.get).mockResolvedValue({ data: approval })

    await expect(getApproval(tenantId, approval.id)).resolves.toMatchObject({
      status: 'pending',
      risk_level: 'external',
    })
  })

  it('列表拒绝未知状态值', async () => {
    vi.mocked(apiClient.get).mockResolvedValue({
      data: {
        items: [{ ...approval, status: 'weird' }],
        total: 1,
        limit: 50,
        offset: 0,
      },
    })

    await expect(listApprovals(tenantId, { view: 'pending' })).rejects.toBeDefined()
  })

  it('批准/拒绝/转交/撤回调用对应端点', async () => {
    vi.mocked(apiClient.post).mockResolvedValue({
      data: { ...approval, status: 'approved' },
    })

    await approveApproval(tenantId, approval.id, { reason: 'ok' })
    expect(apiClient.post).toHaveBeenCalledWith(
      `/approvals/${approval.id}/approve`,
      { reason: 'ok' },
      expect.objectContaining({ headers: { 'X-Tenant-ID': tenantId } }),
    )

    vi.mocked(apiClient.post).mockResolvedValue({
      data: { ...approval, status: 'rejected', reason: 'no' },
    })
    await rejectApproval(tenantId, approval.id, { reason: 'no' })
    expect(apiClient.post).toHaveBeenCalledWith(
      `/approvals/${approval.id}/reject`,
      { reason: 'no' },
      expect.anything(),
    )

    vi.mocked(apiClient.post).mockResolvedValue({
      data: { ...approval, assignee_id: '60000000-0000-4000-8000-000000000060' },
    })
    await transferApproval(tenantId, approval.id, {
      assignee_email: 'admin@example.com',
    })
    expect(apiClient.post).toHaveBeenCalledWith(
      `/approvals/${approval.id}/transfer`,
      { assignee_email: 'admin@example.com' },
      expect.anything(),
    )

    vi.mocked(apiClient.post).mockResolvedValue({
      data: { ...approval, status: 'withdrawn' },
    })
    await withdrawApproval(tenantId, approval.id, { reason: '不需要了' })
    expect(apiClient.post).toHaveBeenCalledWith(
      `/approvals/${approval.id}/withdraw`,
      { reason: '不需要了' },
      expect.anything(),
    )
  })
})
