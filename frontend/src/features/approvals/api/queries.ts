import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'

import { workbenchKeys } from '../../dashboard/api/queries'
import { useActiveWorkspaceId } from '../../workspaces/store'
import {
  approveApproval,
  listApprovals,
  rejectApproval,
  transferApproval,
  withdrawApproval,
  type ApprovalView,
} from './approvals'


export const approvalKeys = {
  list: (tenantId: string, view: ApprovalView, offset: number) =>
    ['approvals', tenantId, view, offset] as const,
  all: (tenantId: string) => ['approvals', tenantId] as const,
}

export function useApprovals(view: ApprovalView, offset = 0) {
  const tenantId = useActiveWorkspaceId()
  return useQuery({
    queryKey: approvalKeys.list(tenantId ?? '', view, offset),
    queryFn: () => listApprovals(tenantId!, { view, offset }),
    enabled: Boolean(tenantId),
    refetchOnMount: 'always',
  })
}

function useApprovalMutation<TPayload>(
  action: (
    tenantId: string,
    approvalId: string,
    payload: TPayload,
  ) => Promise<unknown>,
) {
  const tenantId = useActiveWorkspaceId()
  const queryClient = useQueryClient()
  return useMutation({
    mutationKey: ['approval-action', tenantId],
    mutationFn: ({ approvalId, payload }: { approvalId: string; payload: TPayload }) =>
      action(tenantId!, approvalId, payload),
    onSuccess: async () => {
      await queryClient.invalidateQueries({ queryKey: approvalKeys.all(tenantId ?? '') })
      await queryClient.invalidateQueries({ queryKey: workbenchKeys.summary(tenantId ?? '') })
    },
  })
}

export function useApproveApproval() {
  return useApprovalMutation<{ reason?: string }>(approveApproval)
}

export function useRejectApproval() {
  return useApprovalMutation<{ reason: string }>(rejectApproval)
}

export function useTransferApproval() {
  return useApprovalMutation<{ assignee_email: string; reason?: string }>(transferApproval)
}

export function useWithdrawApproval() {
  return useApprovalMutation<{ reason?: string }>(withdrawApproval)
}
