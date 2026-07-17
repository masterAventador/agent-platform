import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'

import { tenantMutationKey } from '../../../api/tenant'
import { currentUserQueryKey } from '../../auth/api/queries'
import { useActiveWorkspaceId } from '../../workspaces/store'
import {
  acceptInvitation,
  changeMemberRole,
  createInvitation,
  listInvitations,
  listMembers,
  removeMember,
  revokeInvitation,
  transferOwner,
  updateTenantSettings,
  type InvitableRole,
  type TenantRole,
} from './members'

export const memberKeys = {
  list: (tenantId: string) => ['members', tenantId] as const,
}

export const invitationKeys = {
  list: (tenantId: string) => ['invitations', tenantId] as const,
}

export function useMembers() {
  const tenantId = useActiveWorkspaceId()
  return useQuery({
    queryKey: memberKeys.list(tenantId ?? ''),
    queryFn: () => listMembers(tenantId!),
    enabled: Boolean(tenantId),
  })
}

export function useInvitations() {
  const tenantId = useActiveWorkspaceId()
  return useQuery({
    queryKey: invitationKeys.list(tenantId ?? ''),
    queryFn: () => listInvitations(tenantId!),
    enabled: Boolean(tenantId),
  })
}

export function useChangeMemberRole() {
  const tenantId = useActiveWorkspaceId()
  const queryClient = useQueryClient()
  return useMutation({
    mutationKey: tenantMutationKey(tenantId ?? '', 'members', 'change-role'),
    mutationFn: ({ userId, role }: { userId: string, role: TenantRole }) =>
      changeMemberRole(tenantId!, userId, role),
    onSuccess: async () => {
      await queryClient.invalidateQueries({ queryKey: memberKeys.list(tenantId!) })
    },
  })
}

export function useRemoveMember() {
  const tenantId = useActiveWorkspaceId()
  const queryClient = useQueryClient()
  return useMutation({
    mutationKey: tenantMutationKey(tenantId ?? '', 'members', 'remove'),
    mutationFn: (userId: string) => removeMember(tenantId!, userId),
    onSuccess: async () => {
      await queryClient.invalidateQueries({ queryKey: memberKeys.list(tenantId!) })
    },
  })
}

export function useTransferOwner() {
  const tenantId = useActiveWorkspaceId()
  const queryClient = useQueryClient()
  return useMutation({
    mutationKey: tenantMutationKey(tenantId ?? '', 'members', 'transfer-owner'),
    mutationFn: (userId: string) => transferOwner(tenantId!, userId),
    onSuccess: async () => {
      await queryClient.invalidateQueries({ queryKey: memberKeys.list(tenantId!) })
    },
  })
}

export function useUpdateTenantSettings() {
  const tenantId = useActiveWorkspaceId()
  return useMutation({
    mutationKey: tenantMutationKey(tenantId ?? '', 'tenant', 'settings'),
    mutationFn: (name: string) => updateTenantSettings(tenantId!, name),
  })
}

export function useCreateInvitation() {
  const tenantId = useActiveWorkspaceId()
  const queryClient = useQueryClient()
  return useMutation({
    mutationKey: tenantMutationKey(tenantId ?? '', 'invitations', 'create'),
    mutationFn: ({ email, role }: { email: string, role: InvitableRole }) =>
      createInvitation(tenantId!, email, role),
    onSuccess: async () => {
      await queryClient.invalidateQueries({ queryKey: invitationKeys.list(tenantId!) })
    },
  })
}

export function useAcceptInvitation() {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: (token: string) => acceptInvitation(token),
    onSuccess: async () => {
      await queryClient.invalidateQueries({ queryKey: currentUserQueryKey })
    },
  })
}

export function useRevokeInvitation() {
  const tenantId = useActiveWorkspaceId()
  const queryClient = useQueryClient()
  return useMutation({
    mutationKey: tenantMutationKey(tenantId ?? '', 'invitations', 'revoke'),
    mutationFn: (invitationId: string) => revokeInvitation(tenantId!, invitationId),
    onSuccess: async () => {
      await queryClient.invalidateQueries({ queryKey: invitationKeys.list(tenantId!) })
    },
  })
}
