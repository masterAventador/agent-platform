import { beforeEach, describe, expect, it, vi } from 'vitest'

import { apiClient } from '../../../api/client'
import { getWorkbenchSummary } from './workbench'


vi.mock('../../../api/client', () => ({
  apiClient: { get: vi.fn() },
}))

const tenantId = '10000000-0000-4000-8000-000000000010'
const summary = {
  employees: { total: 2, draft: 1, published: 1 },
  runs: {
    total: 7,
    queued: 1,
    running: 1,
    waiting_for_input: 1,
    waiting_for_approval: 1,
    completed: 1,
    failed: 1,
    cancelled: 1,
  },
}

describe('workbench API', () => {
  beforeEach(() => vi.clearAllMocks())

  it('校验并返回租户工作台聚合数据', async () => {
    vi.mocked(apiClient.get).mockResolvedValue({ data: summary })

    await expect(getWorkbenchSummary(tenantId)).resolves.toEqual(summary)
    expect(apiClient.get).toHaveBeenCalledWith('/workbench/summary', {
      headers: { 'X-Tenant-ID': tenantId },
    })
  })

  it.each([
    [{ ...summary, employees: { ...summary.employees, total: -1 } }],
    [{ ...summary, runs: { ...summary.runs, failed: 0.5 } }],
    [{ ...summary, model_usage: { tokens: 10 } }],
  ])('拒绝不合法或当前阶段协议外的统计字段', async (payload) => {
    vi.mocked(apiClient.get).mockResolvedValue({ data: payload })

    await expect(getWorkbenchSummary(tenantId)).rejects.toBeDefined()
  })
})
