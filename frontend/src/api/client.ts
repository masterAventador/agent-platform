import axios from 'axios'

export const apiClient = axios.create({
  baseURL: '/api/v1',
  timeout: 10_000,
  withCredentials: true,
})

export function configureApiBaseUrl(apiBaseUrl: string | null): void {
  apiClient.defaults.baseURL = apiBaseUrl ?? '/api/v1'
}

let sessionRequestController = new AbortController()

apiClient.interceptors.request.use((config) => {
  const requestSignal = config.signal as AbortSignal | undefined
  config.signal = requestSignal
    ? AbortSignal.any([requestSignal, sessionRequestController.signal])
    : sessionRequestController.signal
  return config
})

export function cancelSessionRequests(): void {
  const previousController = sessionRequestController
  sessionRequestController = new AbortController()
  previousController.abort()
}
