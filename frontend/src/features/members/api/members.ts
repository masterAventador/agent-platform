import { z } from 'zod'

import { apiClient } from '../../../api/client'
import { tenantRequestConfig } from '../../../api/tenant'

const uuidSchema = z.uuid()
const dateTimeSchema = z.iso.datetime({ offset: true })
export const tenantRoleSchema = z.enum(['owner', 'admin', 'member'])
export const invitableRoleSchema = z.enum(['admin', 'member'])

export type TenantRole = z.infer<typeof tenantRoleSchema>
export type InvitableRole = z.infer<typeof invitableRoleSchema>

const memberSchema = z.object({
  user_id: uuidSchema,
  email: z.email(),
  display_name: z.string().nullable(),
  role: tenantRoleSchema,
  joined_at: dateTimeSchema,
}).strict()

const memberListSchema = z.array(memberSchema)

const invitationSchema = z.object({
  id: uuidSchema,
  email: z.email(),
  role: tenantRoleSchema,
  status: z.enum(['pending', 'accepted', 'rejected', 'revoked']),
  created_at: dateTimeSchema,
  expires_at: dateTimeSchema,
}).strict()

const invitationListSchema = z.array(invitationSchema)

const createdInvitationSchema = invitationSchema.extend({ token: z.string().min(1) }).strict()

const tenantSettingsSchema = z.object({
  id: uuidSchema,
  name: z.string().min(1),
  slug: z.string().min(1),
}).strict()

export type Member = z.infer<typeof memberSchema>
export type Invitation = z.infer<typeof invitationSchema>
export type CreatedInvitation = z.infer<typeof createdInvitationSchema>
export type TenantSettings = z.infer<typeof tenantSettingsSchema>

export async function listMembers(tenantId: string): Promise<Member[]> {
  const response = await apiClient.get('/tenant/members', tenantRequestConfig(tenantId))
  return memberListSchema.parse(response.data)
}

export async function changeMemberRole(
  tenantId: string,
  userId: string,
  role: TenantRole,
): Promise<Member> {
  const response = await apiClient.patch(
    `/tenant/members/${userId}/role`,
    { role },
    tenantRequestConfig(tenantId),
  )
  return memberSchema.parse(response.data)
}

export async function removeMember(tenantId: string, userId: string): Promise<void> {
  await apiClient.delete(`/tenant/members/${userId}`, tenantRequestConfig(tenantId))
}

export async function transferOwner(tenantId: string, userId: string): Promise<void> {
  await apiClient.post(
    '/tenant/members/transfer-owner',
    { user_id: userId },
    tenantRequestConfig(tenantId),
  )
}

export async function updateTenantSettings(
  tenantId: string,
  name: string,
): Promise<TenantSettings> {
  const response = await apiClient.patch(
    '/tenant/settings',
    { name },
    tenantRequestConfig(tenantId),
  )
  return tenantSettingsSchema.parse(response.data)
}

export async function createInvitation(
  tenantId: string,
  email: string,
  role: InvitableRole,
): Promise<CreatedInvitation> {
  const response = await apiClient.post(
    '/tenant/invitations',
    { email, role },
    tenantRequestConfig(tenantId),
  )
  return createdInvitationSchema.parse(response.data)
}

export async function listInvitations(tenantId: string): Promise<Invitation[]> {
  const response = await apiClient.get('/tenant/invitations', tenantRequestConfig(tenantId))
  return invitationListSchema.parse(response.data)
}

export async function revokeInvitation(tenantId: string, invitationId: string): Promise<void> {
  await apiClient.delete(`/tenant/invitations/${invitationId}`, tenantRequestConfig(tenantId))
}

const acceptResultSchema = z.object({
  status: z.literal('accepted'),
  tenant_id: uuidSchema,
}).strict()

export async function acceptInvitation(token: string): Promise<{ tenantId: string }> {
  const response = await apiClient.post('/invitations/accept', { token })
  const parsed = acceptResultSchema.parse(response.data)
  return { tenantId: parsed.tenant_id }
}

export async function rejectInvitation(token: string): Promise<void> {
  await apiClient.post('/invitations/reject', { token })
}
