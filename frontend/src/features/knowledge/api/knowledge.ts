import { apiClient } from '../../../api/client'
import { tenantRequestConfig } from '../../../api/tenant'


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

export async function listKnowledgeBases(tenantId: string): Promise<KnowledgeBase[]> {
  return (await apiClient.get('/knowledge-bases', tenantRequestConfig(tenantId))).data
}

export async function createKnowledgeBase(
  tenantId: string,
  values: { name: string; description: string },
): Promise<KnowledgeBase> {
  return (await apiClient.post('/knowledge-bases', values, tenantRequestConfig(tenantId))).data
}

export async function deleteKnowledgeBase(
  tenantId: string,
  knowledgeBaseId: string,
): Promise<void> {
  await apiClient.delete(
    `/knowledge-bases/${knowledgeBaseId}`,
    tenantRequestConfig(tenantId),
  )
}

export async function listDocuments(
  tenantId: string,
  knowledgeBaseId: string,
): Promise<KnowledgeDocument[]> {
  return (
    await apiClient.get(`/knowledge-bases/${knowledgeBaseId}/documents`, {
      ...tenantRequestConfig(tenantId),
    })
  ).data
}

export async function uploadDocuments(
  tenantId: string,
  knowledgeBaseId: string,
  files: File[],
): Promise<KnowledgeDocument[]> {
  const body = new FormData()
  files.forEach((file) => body.append('files', file))
  return (
    await apiClient.post(`/knowledge-bases/${knowledgeBaseId}/documents/batch`, body, {
      ...tenantRequestConfig(tenantId),
    })
  ).data
}

export async function retryDocumentParsing(
  tenantId: string,
  knowledgeBaseId: string,
  documentId: string,
): Promise<void> {
  await apiClient.post(
    `/knowledge-bases/${knowledgeBaseId}/documents/${documentId}/retry`,
    {},
    tenantRequestConfig(tenantId),
  )
}

export async function replaceDocument(
  tenantId: string,
  knowledgeBaseId: string,
  documentId: string,
  file: File,
): Promise<KnowledgeDocument> {
  const body = new FormData()
  body.append('file', file)
  return (
    await apiClient.put(`/knowledge-bases/${knowledgeBaseId}/documents/${documentId}`, body, {
      ...tenantRequestConfig(tenantId),
    })
  ).data
}

export async function deleteDocument(
  tenantId: string,
  knowledgeBaseId: string,
  documentId: string,
): Promise<void> {
  await apiClient.delete(
    `/knowledge-bases/${knowledgeBaseId}/documents/${documentId}`,
    tenantRequestConfig(tenantId),
  )
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
      tenantRequestConfig(tenantId),
    )
  ).data
}
