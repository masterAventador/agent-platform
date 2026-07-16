import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'

import { tenantMutationKey } from '../../../api/tenant'
import { useActiveWorkspaceId } from '../../workspaces/store'
import {
  createKnowledgeBase,
  deleteDocument,
  deleteKnowledgeBase,
  listDocuments,
  listKnowledgeBases,
  replaceDocument,
  retrieve,
  retryDocumentParsing,
  uploadDocuments,
} from './knowledge'


const keys = {
  all: (tenantId: string) => ['knowledge-bases', tenantId] as const,
  documents: (tenantId: string, id: string) => ['knowledge-bases', tenantId, id, 'documents'] as const,
}

export function useKnowledgeBases() {
  const tenantId = useActiveWorkspaceId()
  return useQuery({
    queryKey: keys.all(tenantId ?? ''),
    queryFn: () => listKnowledgeBases(tenantId!),
    enabled: Boolean(tenantId),
  })
}

export function useCreateKnowledgeBase() {
  const tenantId = useActiveWorkspaceId()
  const queryClient = useQueryClient()
  return useMutation({
    mutationKey: tenantMutationKey(tenantId ?? '', 'knowledge-bases', 'create'),
    mutationFn: (values: { name: string; description: string }) =>
      createKnowledgeBase(tenantId!, values),
    onSuccess: async () => queryClient.invalidateQueries({ queryKey: keys.all(tenantId!) }),
  })
}

export function useDeleteKnowledgeBase() {
  const tenantId = useActiveWorkspaceId()
  const queryClient = useQueryClient()
  return useMutation({
    mutationKey: tenantMutationKey(tenantId ?? '', 'knowledge-bases', 'delete'),
    mutationFn: (id: string) => deleteKnowledgeBase(tenantId!, id),
    onSuccess: async () => queryClient.invalidateQueries({ queryKey: keys.all(tenantId!) }),
  })
}

export function useKnowledgeDocuments(id: string | undefined) {
  const tenantId = useActiveWorkspaceId()
  return useQuery({
    queryKey: keys.documents(tenantId ?? '', id ?? ''),
    queryFn: () => listDocuments(tenantId!, id!),
    enabled: Boolean(tenantId && id),
    refetchInterval: 3_000,
  })
}

export function useUploadKnowledgeDocuments(id: string) {
  const tenantId = useActiveWorkspaceId()
  const queryClient = useQueryClient()
  return useMutation({
    mutationKey: tenantMutationKey(tenantId ?? '', 'knowledge-bases', 'upload-batch', id),
    mutationFn: (files: File[]) => uploadDocuments(tenantId!, id, files),
    onSuccess: async () =>
      queryClient.invalidateQueries({ queryKey: keys.documents(tenantId!, id) }),
  })
}

export function useRetryKnowledgeDocument(id: string) {
  const tenantId = useActiveWorkspaceId()
  const queryClient = useQueryClient()
  return useMutation({
    mutationKey: tenantMutationKey(tenantId ?? '', 'knowledge-bases', 'retry-document', id),
    mutationFn: (documentId: string) => retryDocumentParsing(tenantId!, id, documentId),
    onSuccess: async () =>
      queryClient.invalidateQueries({ queryKey: keys.documents(tenantId!, id) }),
  })
}

export function useReplaceKnowledgeDocument(id: string) {
  const tenantId = useActiveWorkspaceId()
  const queryClient = useQueryClient()
  return useMutation({
    mutationKey: tenantMutationKey(tenantId ?? '', 'knowledge-bases', 'replace-document', id),
    mutationFn: (payload: { documentId: string; file: File }) =>
      replaceDocument(tenantId!, id, payload.documentId, payload.file),
    onSuccess: async () =>
      queryClient.invalidateQueries({ queryKey: keys.documents(tenantId!, id) }),
  })
}

export function useDeleteKnowledgeDocument(id: string) {
  const tenantId = useActiveWorkspaceId()
  const queryClient = useQueryClient()
  return useMutation({
    mutationKey: tenantMutationKey(tenantId ?? '', 'knowledge-bases', 'delete-document', id),
    mutationFn: (documentId: string) => deleteDocument(tenantId!, id, documentId),
    onSuccess: async () =>
      queryClient.invalidateQueries({ queryKey: keys.documents(tenantId!, id) }),
  })
}

export function useKnowledgeSearch(id: string) {
  const tenantId = useActiveWorkspaceId()
  return useMutation({
    mutationKey: tenantMutationKey(tenantId ?? '', 'knowledge-bases', 'retrieve', id),
    mutationFn: (question: string) => retrieve(tenantId!, id, question),
  })
}
