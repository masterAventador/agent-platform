import axios from 'axios'


export type ClientOperation = 'page' | 'interaction' | 'api' | 'sse' | 'error'
export type ClientOutcome = 'succeeded' | 'failed' | 'denied' | 'timeout'

export type ClientEvent = {
  operation: ClientOperation
  outcome: ClientOutcome
  duration_ms: number
}

export const clientEventClient = axios.create({
  baseURL: '/api/v1',
  timeout: 2_000,
  withCredentials: true,
})

export function configureClientEventBaseUrl(apiBaseUrl: string | null): void {
  clientEventClient.defaults.baseURL = apiBaseUrl ?? '/api/v1'
}

export async function reportClientEvent(
  event: ClientEvent,
  tenantId?: string,
): Promise<void> {
  try {
    await clientEventClient.post('/observability/client-events', event, {
      headers: tenantId ? { 'X-Tenant-ID': tenantId } : undefined,
    })
  } catch {
    // Telemetry must never change the business request result or create an error loop.
  }
}
