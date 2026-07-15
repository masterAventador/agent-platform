import { CanceledError, type AxiosResponse, type GenericAbortSignal } from 'axios'
import { describe, expect, it } from 'vitest'

import { apiClient, cancelSessionRequests, configureApiBaseUrl } from './client'

describe('runtime API base URL', () => {
  it('accepts the validated desktop runtime URL and keeps the web default otherwise', () => {
    configureApiBaseUrl('http://127.0.0.1:18000/api/v1')
    expect(apiClient.defaults.baseURL).toBe('http://127.0.0.1:18000/api/v1')

    configureApiBaseUrl(null)
    expect(apiClient.defaults.baseURL).toBe('/api/v1')
  })
})


describe('session request cancellation', () => {
  it('aborts requests from the old session and gives later requests a fresh signal', async () => {
    let oldSignal: GenericAbortSignal | undefined
    let markStarted: (() => void) | undefined
    const started = new Promise<void>((resolve) => {
      markStarted = resolve
    })
    const oldRequest = apiClient.get('/pending-business-request', {
      adapter: (config) => {
        oldSignal = config.signal ?? undefined
        markStarted?.()
        return new Promise((_, reject) => {
          config.signal?.addEventListener?.('abort', () => reject(new CanceledError()))
        })
      },
    })
    const cancelled = oldRequest.catch((error: unknown) => error)
    await started

    cancelSessionRequests()

    expect(await cancelled).toBeInstanceOf(CanceledError)
    expect(oldSignal?.aborted).toBe(true)

    let freshSignal: GenericAbortSignal | undefined
    await apiClient.get('/new-session-request', {
      adapter: async (config) => {
        freshSignal = config.signal ?? undefined
        return {
          config,
          data: {},
          headers: {},
          status: 200,
          statusText: 'OK',
        } satisfies AxiosResponse
      },
    })
    expect(freshSignal?.aborted).toBe(false)
  })
})
