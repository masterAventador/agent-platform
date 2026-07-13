import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'

import { tenantMutationKey } from '../../../api/tenant'
import { useActiveWorkspaceId } from '../../workspaces/store'
import { controlRun, createRun, getRun, listRunEvents, listRuns } from './runs'


export const runKeys = {
  all: (tenantId: string) => ['runs', tenantId] as const,
  detail: (tenantId: string, runId: string) => ['runs', tenantId, runId] as const,
  events: (tenantId: string, runId: string) => ['runs', tenantId, runId, 'events'] as const,
}

export function useRuns() {
  const tenantId = useActiveWorkspaceId()
  return useQuery({
    queryKey: runKeys.all(tenantId ?? ''),
    queryFn: () => listRuns(tenantId!),
    enabled: Boolean(tenantId),
  })
}

export function useRun(runId: string | undefined) {
  const tenantId = useActiveWorkspaceId()
  return useQuery({
    queryKey: runKeys.detail(tenantId ?? '', runId ?? ''),
    queryFn: () => getRun(tenantId!, runId!),
    enabled: Boolean(tenantId && runId),
  })
}

export function useRunEvents(runId: string | undefined) {
  const tenantId = useActiveWorkspaceId()
  return useQuery({
    queryKey: runKeys.events(tenantId ?? '', runId ?? ''),
    queryFn: () => listRunEvents(tenantId!, runId!),
    enabled: Boolean(tenantId && runId),
  })
}

export function useCreateRun(employeeId: string) {
  const tenantId = useActiveWorkspaceId()
  const queryClient = useQueryClient()
  return useMutation({
    mutationKey: tenantMutationKey(tenantId ?? '', 'runs', 'create', employeeId),
    mutationFn: (input: Record<string, unknown>) => createRun(tenantId!, employeeId, input),
    onSuccess: async () => queryClient.invalidateQueries({ queryKey: runKeys.all(tenantId!) }),
  })
}

export function useControlRun(runId: string) {
  const tenantId = useActiveWorkspaceId()
  const queryClient = useQueryClient()
  return useMutation({
    mutationKey: tenantMutationKey(tenantId ?? '', 'runs', 'control', runId),
    mutationFn: ({
      action,
      approvalId,
      reason,
    }: {
      action: 'resume' | 'cancel' | 'approve' | 'reject'
      approvalId?: string
      reason?: string
    }) => controlRun(tenantId!, runId, action, { approval_id: approvalId, reason }),
    onSuccess: async (run) => {
      queryClient.setQueryData(runKeys.detail(tenantId!, runId), run)
      await Promise.all([
        queryClient.invalidateQueries({ queryKey: runKeys.all(tenantId!) }),
        queryClient.invalidateQueries({ queryKey: runKeys.events(tenantId!, runId) }),
      ])
    },
  })
}
