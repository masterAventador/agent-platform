import axios from 'axios'

import {
  configureClientEventBaseUrl,
  reportClientEvent,
} from '../observability/client-events'

export const apiClient = axios.create({
  baseURL: '/api/v1',
  timeout: 10_000,
  withCredentials: true,
})

export function configureApiBaseUrl(apiBaseUrl: string | null): void {
  apiClient.defaults.baseURL = apiBaseUrl ?? '/api/v1'
  configureClientEventBaseUrl(apiBaseUrl)
}

let sessionRequestController = new AbortController()
const requestStartedAt = new WeakMap<object, number>()

apiClient.interceptors.request.use((config) => {
  requestStartedAt.set(config, Date.now())
  const requestSignal = config.signal as AbortSignal | undefined
  config.signal = requestSignal
    ? AbortSignal.any([requestSignal, sessionRequestController.signal])
    : sessionRequestController.signal
  return config
})

apiClient.interceptors.response.use(
  (response) => {
    void reportClientEvent(
      {
        operation: 'api',
        outcome: 'succeeded',
        duration_ms: Math.max(0, Date.now() - (requestStartedAt.get(response.config) ?? Date.now())),
      },
      response.config.headers.get('X-Tenant-ID')?.toString(),
    )
    return response
  },
  (error: unknown) => {
    if (axios.isAxiosError(error) && error.config && !axios.isCancel(error)) {
      void reportClientEvent(
        {
          operation: 'api',
          outcome: error.code === 'ECONNABORTED' ? 'timeout' : 'failed',
          duration_ms: Math.max(0, Date.now() - (requestStartedAt.get(error.config) ?? Date.now())),
        },
        error.config.headers.get('X-Tenant-ID')?.toString(),
      )
    }
    return Promise.reject(error)
  },
)

export function cancelSessionRequests(): void {
  const previousController = sessionRequestController
  sessionRequestController = new AbortController()
  previousController.abort()
}
