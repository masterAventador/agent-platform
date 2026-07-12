import { apiClient } from '../../../api/client'


export interface KnowledgeBase {
  id: string
  tenant_id: string
  name: string
  description: string
  provider: 'ragflow'
}

export interface KnowledgeDocument {
  provider_id: string
  name: string
  status: string
  size_bytes: number
  chunk_count: number
}

export interface KnowledgeCitation {
  chunk_id: string
  document_id: string
  document_name: string
  dataset_id: string
  content: string
  score: number
  metadata: Record<string, unknown>
}

const headers = (tenantId: string) => ({ 'X-Tenant-ID': tenantId })

export async function listKnowledgeBases(tenantId: string): Promise<KnowledgeBase[]> {
  return (await apiClient.get('/knowledge-bases', { headers: headers(tenantId) })).data
}

export async function createKnowledgeBase(
  tenantId: string,
  values: { name: string; description: string },
): Promise<KnowledgeBase> {
  return (await apiClient.post('/knowledge-bases', values, { headers: headers(tenantId) })).data
}

export async function listDocuments(
  tenantId: string,
  knowledgeBaseId: string,
): Promise<KnowledgeDocument[]> {
  return (
    await apiClient.get(`/knowledge-bases/${knowledgeBaseId}/documents`, {
      headers: headers(tenantId),
    })
  ).data
}

export async function uploadDocument(
  tenantId: string,
  knowledgeBaseId: string,
  file: File,
): Promise<KnowledgeDocument> {
  const body = new FormData()
  body.append('file', file)
  return (
    await apiClient.post(`/knowledge-bases/${knowledgeBaseId}/documents`, body, {
      headers: headers(tenantId),
    })
  ).data
}

export async function retrieve(
  tenantId: string,
  knowledgeBaseId: string,
  question: string,
): Promise<{ total: number; citations: KnowledgeCitation[] }> {
  return (
    await apiClient.post(
      `/knowledge-bases/${knowledgeBaseId}/retrieve`,
      { question },
      { headers: headers(tenantId) },
    )
  ).data
}
