import { apiClient } from '../../../api/client'
import { tenantRequestConfig } from '../../../api/tenant'


export type MemoryScope = 'tenant' | 'user' | 'employee' | 'conversation'
export type MemoryStatus = 'active' | 'disabled'
export type MemorySource = 'run' | 'conversation' | 'manual'

export interface Memory {
  id: string
  tenant_id: string
  scope: MemoryScope
  scope_ref: string
  content: string
  source: MemorySource
  source_ref: string | null
  confidence: number
  status: MemoryStatus
  expired: boolean
  expires_at: string | null
  created_by: string | null
  created_at: string
  updated_at: string
}

export interface ListMemoriesFilters {
  scope?: MemoryScope
  q?: string
  activeOnly?: boolean
}

export interface CreateMemoryInput {
  scope: MemoryScope
  scopeRef?: string
  content: string
  expiresAt?: string
}

export interface UpdateMemoryInput {
  content?: string
  status?: MemoryStatus
}

export async function listMemories(
  tenantId: string,
  filters: ListMemoriesFilters,
): Promise<Memory[]> {
  const params: Record<string, string | boolean> = {}
  if (filters.scope) params.scope = filters.scope
  if (filters.q) params.q = filters.q
  if (filters.activeOnly) params.active_only = true
  return (
    await apiClient.get<Memory[]>('/memories', {
      ...tenantRequestConfig(tenantId),
      params,
    })
  ).data
}

export async function createMemory(
  tenantId: string,
  input: CreateMemoryInput,
): Promise<Memory> {
  const payload: Record<string, string> = {
    scope: input.scope,
    content: input.content,
  }
  if (input.scopeRef) payload.scope_ref = input.scopeRef
  if (input.expiresAt) payload.expires_at = input.expiresAt
  return (
    await apiClient.post<Memory>('/memories', payload, tenantRequestConfig(tenantId))
  ).data
}

export async function updateMemory(
  tenantId: string,
  memoryId: string,
  input: UpdateMemoryInput,
): Promise<Memory> {
  const payload: Record<string, string> = {}
  if (input.content !== undefined) payload.content = input.content
  if (input.status !== undefined) payload.status = input.status
  return (
    await apiClient.patch<Memory>(
      `/memories/${memoryId}`,
      payload,
      tenantRequestConfig(tenantId),
    )
  ).data
}

export async function deleteMemory(tenantId: string, memoryId: string): Promise<void> {
  await apiClient.delete(`/memories/${memoryId}`, tenantRequestConfig(tenantId))
}
