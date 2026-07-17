import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'

import { tenantMutationKey } from '../../../api/tenant'
import { useActiveWorkspaceId } from '../../workspaces/store'
import {
  addWorkflowVersion,
  listWorkflows,
  listWorkflowVersions,
  publishWorkflow,
  registerWorkflow,
  rollbackWorkflow,
  type AddWorkflowVersionRequest,
  type RegisterWorkflowRequest,
} from './workflows'


export const workflowKeys = {
  all: (tenantId: string) => ['workflows', tenantId] as const,
  versions: (tenantId: string, workflowId: string) =>
    ['workflows', tenantId, workflowId, 'versions'] as const,
}


export function useWorkflows() {
  const tenantId = useActiveWorkspaceId()
  return useQuery({
    queryKey: workflowKeys.all(tenantId ?? ''),
    queryFn: () => listWorkflows(tenantId!),
    enabled: Boolean(tenantId),
  })
}


export function useWorkflowVersions(workflowId: string | undefined) {
  const tenantId = useActiveWorkspaceId()
  return useQuery({
    queryKey: workflowKeys.versions(tenantId ?? '', workflowId ?? ''),
    queryFn: () => listWorkflowVersions(tenantId!, workflowId!),
    enabled: Boolean(tenantId && workflowId),
  })
}


export function useRegisterWorkflow() {
  const tenantId = useActiveWorkspaceId()
  const queryClient = useQueryClient()
  return useMutation({
    mutationKey: tenantMutationKey(tenantId ?? '', 'workflows', 'register'),
    mutationFn: (request: RegisterWorkflowRequest) => registerWorkflow(tenantId!, request),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: workflowKeys.all(tenantId ?? '') })
    },
  })
}


export function useAddWorkflowVersion(workflowId: string) {
  const tenantId = useActiveWorkspaceId()
  const queryClient = useQueryClient()
  return useMutation({
    mutationKey: tenantMutationKey(tenantId ?? '', 'workflows', workflowId, 'version'),
    mutationFn: (request: AddWorkflowVersionRequest) =>
      addWorkflowVersion(tenantId!, workflowId, request),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: workflowKeys.all(tenantId ?? '') })
      void queryClient.invalidateQueries({
        queryKey: workflowKeys.versions(tenantId ?? '', workflowId),
      })
    },
  })
}


export function usePublishWorkflow(workflowId: string) {
  const tenantId = useActiveWorkspaceId()
  const queryClient = useQueryClient()
  return useMutation({
    mutationKey: tenantMutationKey(tenantId ?? '', 'workflows', workflowId, 'publish'),
    mutationFn: (version: number) => publishWorkflow(tenantId!, workflowId, version),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: workflowKeys.all(tenantId ?? '') })
    },
  })
}


export function useRollbackWorkflow(workflowId: string) {
  const tenantId = useActiveWorkspaceId()
  const queryClient = useQueryClient()
  return useMutation({
    mutationKey: tenantMutationKey(tenantId ?? '', 'workflows', workflowId, 'rollback'),
    mutationFn: (version: number) => rollbackWorkflow(tenantId!, workflowId, version),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: workflowKeys.all(tenantId ?? '') })
    },
  })
}
