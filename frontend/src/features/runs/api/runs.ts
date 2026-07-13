import { apiClient } from '../../../api/client'
import { tenantRequestConfig } from '../../../api/tenant'


export type RunStatus =
  | 'queued'
  | 'running'
  | 'waiting_for_input'
  | 'waiting_for_approval'
  | 'completed'
  | 'failed'
  | 'cancelled'

export interface Run {
  id: string
  tenant_id: string
  employee_id: string
  employee_version: number
  thread_id: string
  input: Record<string, unknown>
  status: RunStatus
  error_code: string | null
  error_message: string | null
}

export interface RunEvent {
  event_id: string
  event_version: '1.0'
  tenant_id: string
  employee_id: string
  run_id: string
  sequence: number
  type: string
  occurred_at: string
  payload: Record<string, unknown>
}

export async function createRun(
  tenantId: string,
  employeeId: string,
  input: Record<string, unknown>,
): Promise<Run> {
  const response = await apiClient.post<Run>(
    `/employees/${employeeId}/runs`,
    { input },
    tenantRequestConfig(tenantId),
  )
  return response.data
}

export async function listRuns(tenantId: string): Promise<Run[]> {
  return (await apiClient.get<Run[]>('/runs', tenantRequestConfig(tenantId))).data
}

export async function getRun(tenantId: string, runId: string): Promise<Run> {
  return (await apiClient.get<Run>(`/runs/${runId}`, tenantRequestConfig(tenantId))).data
}

export async function listRunEvents(tenantId: string, runId: string): Promise<RunEvent[]> {
  return (
    await apiClient.get<RunEvent[]>(`/runs/${runId}/events`, {
      ...tenantRequestConfig(tenantId),
    })
  ).data
}

export async function controlRun(
  tenantId: string,
  runId: string,
  action: 'resume' | 'cancel' | 'approve' | 'reject',
  options: { approval_id?: string; reason?: string } = {},
): Promise<Run> {
  return (
    await apiClient.post<Run>(
      `/runs/${runId}/control`,
      { action, ...options },
      tenantRequestConfig(tenantId),
    )
  ).data
}
