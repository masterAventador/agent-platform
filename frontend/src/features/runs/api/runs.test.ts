import { beforeEach, describe, expect, it, vi } from 'vitest'

import { apiClient } from '../../../api/client'
import { createRun } from './runs'


vi.mock('../../../api/client', () => ({
  apiClient: {
    post: vi.fn(),
  },
}))

describe('run submission boundary', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    vi.mocked(apiClient.post).mockResolvedValue({ data: { id: 'run-1' } })
  })

  it('sends one stable idempotency key with run creation', async () => {
    await createRun(
      'tenant-1',
      'employee-1',
      { message: 'hello' },
      ['file-1'],
      '00000000-0000-4000-8000-000000000123',
    )

    expect(apiClient.post).toHaveBeenCalledWith(
      '/employees/employee-1/runs',
      { input: { message: 'hello' }, attachment_ids: ['file-1'] },
      {
        headers: {
          'X-Tenant-ID': 'tenant-1',
          'Idempotency-Key': '00000000-0000-4000-8000-000000000123',
        },
      },
    )
  })
})
