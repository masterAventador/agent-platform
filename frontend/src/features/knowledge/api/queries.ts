import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'

import { tenantMutationKey } from '../../../api/tenant'
import { useActiveWorkspaceId } from '../../workspaces/store'
import {
  createKnowledgeBase,
  deleteKnowledgeBase,
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

export function useUploadKnowledgeDocument(id: string) {
  const tenantId = useActiveWorkspaceId()
  const queryClient = useQueryClient()
  return useMutation({
    mutationKey: tenantMutationKey(tenantId ?? '', 'knowledge-bases', 'upload', id),
    mutationFn: (file: File) => uploadDocument(tenantId!, id, file),
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
