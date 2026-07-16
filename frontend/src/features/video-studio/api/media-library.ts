import { z } from 'zod'

import { apiClient } from '../../../api/client'
import { tenantRequestConfig } from '../../../api/tenant'

const materialKindSchema = z.enum(['video', 'image', 'music'])
const materialFolderSchema = z.object({
  id: z.uuid(),
  parent_id: z.uuid().nullable(),
  name: z.string().min(1).max(255),
  created_by: z.uuid(),
  created_at: z.iso.datetime({ offset: true }),
}).strict()
const materialFolderListResponseSchema = z.object({
  items: z.array(materialFolderSchema),
}).strict()
const materialSchema = z.object({
  id: z.uuid(),
  folder_id: z.uuid().nullable(),
  name: z.string().min(1).max(255),
  kind: materialKindSchema,
  media_type: z.string().min(1).max(255),
  size_bytes: z.number().int().positive(),
  sha256: z.string().regex(/^[0-9a-f]{64}$/),
  storage_key: z.string().min(1).max(700),
  status: z.enum(['pending_upload', 'available', 'upload_failed', 'deleted']),
  tags: z.array(z.string()),
  cleanup_required: z.boolean(),
  artifact_id: z.uuid().nullable(),
  created_at: z.iso.datetime({ offset: true }),
  updated_at: z.iso.datetime({ offset: true }),
}).strict()
const uploadCredentialsSchema = z.object({
  provider: z.literal('tencent-cos'),
  bucket: z.string().min(1),
  region: z.string().min(1),
  key_prefix: z.string().min(1),
  tmp_secret_id: z.string().min(1),
  tmp_secret_key: z.string().min(1),
  session_token: z.string().min(1),
  expires_at: z.iso.datetime({ offset: true }),
}).strict()
const uploadCredentialResponseSchema = z.object({
  material: materialSchema,
  credentials: uploadCredentialsSchema,
}).strict()
const materialListResponseSchema = z.object({ items: z.array(materialSchema) }).strict()
const materialPreviewSchema = z.object({
  url: z.url(),
  expires_at: z.iso.datetime({ offset: true }),
}).strict()
const downloadTaskSchema = z.object({
  id: z.uuid(),
  source_type: z.enum(['material', 'artifact']),
  source_id: z.uuid(),
  status: z.enum(['queued', 'running', 'succeeded', 'failed', 'cancelled']),
  progress: z.number().int().min(0).max(100),
  downloaded_bytes: z.number().int().nonnegative(),
  total_bytes: z.number().int().nonnegative(),
  resume_token: z.string().nullable(),
  error_code: z.string().nullable(),
  retryable: z.boolean(),
  retry_count: z.number().int().nonnegative(),
  revision: z.number().int().nonnegative(),
  created_at: z.iso.datetime({ offset: true }),
  updated_at: z.iso.datetime({ offset: true }),
  completed_at: z.iso.datetime({ offset: true }).nullable(),
}).strict()
const downloadTaskListResponseSchema = z.object({ items: z.array(downloadTaskSchema) }).strict()

export type MaterialKind = z.infer<typeof materialKindSchema>
export type MaterialFolder = z.infer<typeof materialFolderSchema>
export type VideoMaterial = z.infer<typeof materialSchema>
export type MaterialUploadCredentials = z.infer<typeof uploadCredentialsSchema>
export type MaterialUploadCredentialResponse = z.infer<typeof uploadCredentialResponseSchema>
export type MaterialPreview = z.infer<typeof materialPreviewSchema>
export type DownloadTask = z.infer<typeof downloadTaskSchema>

export interface MaterialUploadCredentialInput {
  name: string
  kind: MaterialKind
  media_type: string
  size_bytes: number
  sha256: string
  folder_id?: string | null
  tag_names?: string[]
}

export async function listMaterialFolders(tenantId: string): Promise<MaterialFolder[]> {
  const response = await apiClient.get('/video-studio/material-folders', tenantRequestConfig(tenantId))
  return materialFolderListResponseSchema.parse(response.data).items
}

export async function createMaterialFolder(
  tenantId: string,
  input: { name: string; parent_id: string | null },
): Promise<MaterialFolder> {
  const response = await apiClient.post(
    '/video-studio/material-folders',
    input,
    tenantRequestConfig(tenantId),
  )
  return materialFolderSchema.parse(response.data)
}

export async function listMaterials(tenantId: string): Promise<VideoMaterial[]> {
  const response = await apiClient.get('/video-studio/materials', tenantRequestConfig(tenantId))
  return materialListResponseSchema.parse(response.data).items
}

export async function requestMaterialUploadCredentials(
  tenantId: string,
  input: MaterialUploadCredentialInput,
): Promise<MaterialUploadCredentialResponse> {
  const response = await apiClient.post(
    '/video-studio/materials/upload-credentials',
    input,
    tenantRequestConfig(tenantId),
  )
  return uploadCredentialResponseSchema.parse(response.data)
}

export async function completeMaterialUpload(
  tenantId: string,
  materialId: string,
): Promise<VideoMaterial> {
  const response = await apiClient.post(
    `/video-studio/materials/${materialId}/complete-upload`,
    {},
    tenantRequestConfig(tenantId),
  )
  return materialSchema.parse(response.data)
}

export async function abortMaterialUpload(
  tenantId: string,
  materialId: string,
): Promise<VideoMaterial> {
  const response = await apiClient.post(
    `/video-studio/materials/${materialId}/abort-upload`,
    {},
    tenantRequestConfig(tenantId),
  )
  return materialSchema.parse(response.data)
}

export async function listDownloadTasks(tenantId: string): Promise<DownloadTask[]> {
  const response = await apiClient.get('/video-studio/download-tasks', tenantRequestConfig(tenantId))
  return downloadTaskListResponseSchema.parse(response.data).items
}

export async function requestMaterialPreview(
  tenantId: string,
  materialId: string,
): Promise<MaterialPreview> {
  const response = await apiClient.get(
    `/video-studio/materials/${materialId}/preview`,
    tenantRequestConfig(tenantId),
  )
  return materialPreviewSchema.parse(response.data)
}

export async function deleteMaterial(tenantId: string, materialId: string): Promise<void> {
  await apiClient.delete(
    `/video-studio/materials/${materialId}`,
    tenantRequestConfig(tenantId),
  )
}

export async function createDownloadTask(
  tenantId: string,
  input: { source_type: 'material' | 'artifact'; source_id: string },
): Promise<DownloadTask> {
  const response = await apiClient.post(
    '/video-studio/download-tasks',
    input,
    tenantRequestConfig(tenantId),
  )
  return downloadTaskSchema.parse(response.data)
}

export async function retryDownloadTask(
  tenantId: string,
  taskId: string,
): Promise<DownloadTask> {
  const response = await apiClient.post(
    `/video-studio/download-tasks/${taskId}/retry`,
    {},
    tenantRequestConfig(tenantId),
  )
  return downloadTaskSchema.parse(response.data)
}
