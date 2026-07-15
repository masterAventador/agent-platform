import { apiClient } from '../../../api/client'
import { tenantRequestConfig } from '../../../api/tenant'
import type { PlatformFile } from '../../../platform'


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

export interface StoredFile {
  id: string
  name: string
  media_type: string
  size_bytes: number
  sha256: string
}

export interface Artifact {
  id: string
  run_id: string
  name: string
  media_type: string
  size_bytes: number
  sha256: string
  created_at: string
}

const mediaTypes: Record<string, string> = {
  csv: 'text/csv',
  docx: 'application/vnd.openxmlformats-officedocument.wordprocessingml.document',
  jpeg: 'image/jpeg',
  jpg: 'image/jpeg',
  json: 'application/json',
  md: 'text/markdown',
  pdf: 'application/pdf',
  png: 'image/png',
  txt: 'text/plain',
}

function fileMediaType(name: string): string {
  const extension = name.split('.').at(-1)?.toLowerCase() ?? ''
  return mediaTypes[extension] ?? 'application/octet-stream'
}

export async function uploadFile(tenantId: string, file: PlatformFile): Promise<StoredFile> {
  const form = new FormData()
  form.append(
    'file',
    new Blob([Uint8Array.from(file.bytes).buffer], { type: fileMediaType(file.name) }),
    file.name,
  )
  const response = await apiClient.post<StoredFile>('/files', form, tenantRequestConfig(tenantId))
  return response.data
}

export async function deleteUnboundFile(
  tenantId: string,
  fileId: string,
): Promise<{ deleted: boolean }> {
  return (
    await apiClient.delete<{ deleted: boolean }>(
      `/files/${fileId}`,
      tenantRequestConfig(tenantId),
    )
  ).data
}

export async function createRun(
  tenantId: string,
  employeeId: string,
  input: Record<string, unknown>,
  attachmentIds: string[] = [],
  idempotencyKey?: string,
): Promise<Run> {
  const config = tenantRequestConfig(tenantId)
  const response = await apiClient.post<Run>(
    `/employees/${employeeId}/runs`,
    { input, attachment_ids: attachmentIds },
    idempotencyKey
      ? { ...config, headers: { ...config.headers, 'Idempotency-Key': idempotencyKey } }
      : config,
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

export async function listArtifacts(tenantId: string, runId: string): Promise<Artifact[]> {
  return (
    await apiClient.get<Artifact[]>(
      `/runs/${runId}/artifacts`,
      tenantRequestConfig(tenantId),
    )
  ).data
}

export async function downloadArtifact(
  tenantId: string,
  artifactId: string,
): Promise<Uint8Array> {
  const response = await apiClient.get<ArrayBuffer>(`/artifacts/${artifactId}/content`, {
    ...tenantRequestConfig(tenantId),
    responseType: 'arraybuffer',
  })
  return new Uint8Array(response.data)
}

export async function deleteArtifact(tenantId: string, artifactId: string): Promise<void> {
  await apiClient.delete(`/artifacts/${artifactId}`, tenantRequestConfig(tenantId))
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
