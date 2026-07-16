import type { AxiosResponse } from 'axios'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import {
  clientEventClient,
  configureClientEventBaseUrl,
  reportClientEvent,
} from './client-events'


describe('client observability events', () => {
  const requests: Array<{ url?: string, data?: string, tenantId?: string }> = []

  beforeEach(() => {
    requests.length = 0
    configureClientEventBaseUrl(null)
    clientEventClient.defaults.adapter = async (config) => {
      requests.push({
        url: config.url,
        data: config.data as string | undefined,
        tenantId: config.headers.get('X-Tenant-ID')?.toString(),
      })
      return {
        config,
        data: undefined,
        headers: {},
        status: 204,
        statusText: 'No Content',
      } satisfies AxiosResponse
    }
  })

  it('只上报固定维度和耗时，不携带业务内容或动态标识', async () => {
    await reportClientEvent(
      { operation: 'api', outcome: 'failed', duration_ms: 12 },
      '10000000-0000-4000-8000-000000000010',
    )

    expect(requests).toHaveLength(1)
    expect(requests[0]).toMatchObject({
      url: '/observability/client-events',
      tenantId: '10000000-0000-4000-8000-000000000010',
    })
    expect(JSON.parse(requests[0]?.data ?? '{}')).toEqual({
      operation: 'api',
      outcome: 'failed',
      duration_ms: 12,
    })
  })

  it('上报失败不会冒泡干扰业务，并跟随桌面 API 基址', async () => {
    configureClientEventBaseUrl('http://127.0.0.1:18000/api/v1')
    expect(clientEventClient.defaults.baseURL).toBe('http://127.0.0.1:18000/api/v1')
    clientEventClient.defaults.adapter = vi.fn().mockRejectedValue(new Error('offline'))

    await expect(
      reportClientEvent({ operation: 'error', outcome: 'failed', duration_ms: 0 }),
    ).resolves.toBeUndefined()
  })
})
