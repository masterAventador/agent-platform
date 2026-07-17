import { z } from 'zod'

import { apiClient } from '../../../api/client'
import { tenantRequestConfig } from '../../../api/tenant'


export const approvalStatuses = [
  'pending',
  'approved',
  'rejected',
  'expired',
  'withdrawn',
  'transferred',
] as const

const approvalSchema = z.object({
  id: z.uuid(),
  tenant_id: z.uuid(),
  source: z.string(),
  approval_type: z.string(),
  risk_level: z.string(),
  status: z.enum(approvalStatuses),
  requested_by: z.uuid(),
  required_role: z.string(),
  context: z.record(z.string(), z.unknown()),
  run_id: z.uuid().nullable(),
  invocation_id: z.uuid().nullable(),
  employee_id: z.uuid().nullable(),
  assignee_id: z.uuid().nullable(),
  decided_by: z.uuid().nullable(),
  reason: z.string().nullable(),
  decided_at: z.string().nullable(),
  created_at: z.string(),
  expires_at: z.string().nullable(),
  transferred_from_id: z.uuid().nullable(),
  transferred_to_id: z.uuid().nullable(),
  revision: z.number().int().positive(),
})

const approvalListSchema = z.object({
  items: z.array(approvalSchema),
  total: z.number().int().nonnegative(),
  limit: z.number().int().positive(),
  offset: z.number().int().nonnegative(),
})

export type Approval = z.infer<typeof approvalSchema>
export type ApprovalStatus = Approval['status']
export type ApprovalList = z.infer<typeof approvalListSchema>
export type ApprovalView = 'pending' | 'history'

export interface ListApprovalsParams {
  view: ApprovalView
  limit?: number
  offset?: number
}

export async function listApprovals(
  tenantId: string,
  params: ListApprovalsParams,
): Promise<ApprovalList> {
  const response = await apiClient.get<unknown>('/approvals', {
    ...tenantRequestConfig(tenantId),
    params: {
      view: params.view,
      limit: params.limit ?? 50,
      offset: params.offset ?? 0,
    },
  })
  return approvalListSchema.parse(response.data)
}

export async function getApproval(tenantId: string, approvalId: string): Promise<Approval> {
  const response = await apiClient.get<unknown>(
    `/approvals/${approvalId}`,
    tenantRequestConfig(tenantId),
  )
  return approvalSchema.parse(response.data)
}

export async function approveApproval(
  tenantId: string,
  approvalId: string,
  payload: { reason?: string },
): Promise<Approval> {
  const response = await apiClient.post<unknown>(
    `/approvals/${approvalId}/approve`,
    payload,
    tenantRequestConfig(tenantId),
  )
  return approvalSchema.parse(response.data)
}

export async function rejectApproval(
  tenantId: string,
  approvalId: string,
  payload: { reason: string },
): Promise<Approval> {
  const response = await apiClient.post<unknown>(
    `/approvals/${approvalId}/reject`,
    payload,
    tenantRequestConfig(tenantId),
  )
  return approvalSchema.parse(response.data)
}

export async function transferApproval(
  tenantId: string,
  approvalId: string,
  payload: { assignee_email: string; reason?: string },
): Promise<Approval> {
  const response = await apiClient.post<unknown>(
    `/approvals/${approvalId}/transfer`,
    payload,
    tenantRequestConfig(tenantId),
  )
  return approvalSchema.parse(response.data)
}

export async function withdrawApproval(
  tenantId: string,
  approvalId: string,
  payload: { reason?: string },
): Promise<Approval> {
  const response = await apiClient.post<unknown>(
    `/approvals/${approvalId}/withdraw`,
    payload,
    tenantRequestConfig(tenantId),
  )
  return approvalSchema.parse(response.data)
}
