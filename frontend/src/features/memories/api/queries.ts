import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'

import { tenantMutationKey } from '../../../api/tenant'
import { useActiveWorkspaceId } from '../../workspaces/store'
import {
  createMemory,
  deleteMemory,
  listMemories,
  updateMemory,
  type CreateMemoryInput,
  type ListMemoriesFilters,
  type UpdateMemoryInput,
} from './memories'


export const memoryKeys = {
  all: (tenantId: string) => ['memories', tenantId] as const,
  list: (tenantId: string, filters: ListMemoriesFilters) => [
    'memories',
    tenantId,
    filters.scope ?? 'all',
    filters.q ?? '',
    Boolean(filters.activeOnly),
  ] as const,
}

export function useMemories(filters: ListMemoriesFilters) {
  const tenantId = useActiveWorkspaceId()
  return useQuery({
    queryKey: memoryKeys.list(tenantId ?? '', filters),
    queryFn: () => listMemories(tenantId!, filters),
    enabled: Boolean(tenantId),
  })
}

function useInvalidateMemories() {
  const tenantId = useActiveWorkspaceId()
  const queryClient = useQueryClient()
  return async () => {
    await queryClient.invalidateQueries({ queryKey: memoryKeys.all(tenantId!) })
  }
}

export function useCreateMemory() {
  const tenantId = useActiveWorkspaceId()
  const invalidate = useInvalidateMemories()
  return useMutation({
    mutationKey: tenantMutationKey(tenantId ?? '', 'memories', 'create'),
    mutationFn: (input: CreateMemoryInput) => createMemory(tenantId!, input),
    onSuccess: invalidate,
  })
}

export function useUpdateMemory() {
  const tenantId = useActiveWorkspaceId()
  const invalidate = useInvalidateMemories()
  return useMutation({
    mutationKey: tenantMutationKey(tenantId ?? '', 'memories', 'update'),
    mutationFn: ({ memoryId, input }: { memoryId: string; input: UpdateMemoryInput }) => (
      updateMemory(tenantId!, memoryId, input)
    ),
    onSuccess: invalidate,
  })
}

export function useDeleteMemory() {
  const tenantId = useActiveWorkspaceId()
  const invalidate = useInvalidateMemories()
  return useMutation({
    mutationKey: tenantMutationKey(tenantId ?? '', 'memories', 'delete'),
    mutationFn: (memoryId: string) => deleteMemory(tenantId!, memoryId),
    onSuccess: invalidate,
  })
}
