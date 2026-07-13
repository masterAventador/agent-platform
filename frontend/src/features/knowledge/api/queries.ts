import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'

import { useActiveWorkspaceId } from '../../workspaces/store'
import {
  createKnowledgeBase,
  listDocuments,
  listKnowledgeBases,
  retrieve,
  uploadDocument,
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
    mutationFn: (values: { name: string; description: string }) =>
      createKnowledgeBase(tenantId!, values),
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

export function useUploadKnowledgeDocument(id: string) {
  const tenantId = useActiveWorkspaceId()
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: (file: File) => uploadDocument(tenantId!, id, file),
    onSuccess: async () =>
      queryClient.invalidateQueries({ queryKey: keys.documents(tenantId!, id) }),
  })
}

export function useKnowledgeSearch(id: string) {
  const tenantId = useActiveWorkspaceId()
  return useMutation({ mutationFn: (question: string) => retrieve(tenantId!, id, question) })
}
