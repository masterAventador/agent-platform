import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'

import { currentUserQueryKey } from '../../auth/api/queries'
import {
  changePassword,
  confirmEmailVerification,
  getProfile,
  listSessions,
  requestEmailVerification,
  revokeOtherSessions,
  revokeSession,
  updateProfile,
} from './account'

export const accountKeys = {
  profile: ['account', 'profile'] as const,
  sessions: ['account', 'sessions'] as const,
}

export function useProfile() {
  return useQuery({ queryKey: accountKeys.profile, queryFn: getProfile })
}

export function useSessions() {
  return useQuery({ queryKey: accountKeys.sessions, queryFn: listSessions })
}

export function useUpdateProfile() {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: (displayName: string | null) => updateProfile(displayName),
    onSuccess: async () => {
      await queryClient.invalidateQueries({ queryKey: accountKeys.profile })
    },
  })
}

export function useChangePassword() {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: ({ currentPassword, newPassword }: {
      currentPassword: string
      newPassword: string
    }) => changePassword(currentPassword, newPassword),
    onSuccess: async () => {
      await queryClient.invalidateQueries({ queryKey: accountKeys.sessions })
    },
  })
}

export function useRequestEmailVerification() {
  return useMutation({ mutationFn: requestEmailVerification })
}

export function useConfirmEmailVerification() {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: (token: string) => confirmEmailVerification(token),
    onSuccess: async () => {
      await queryClient.invalidateQueries({ queryKey: accountKeys.profile })
      await queryClient.invalidateQueries({ queryKey: currentUserQueryKey })
    },
  })
}

export function useRevokeSession() {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: (sessionId: string) => revokeSession(sessionId),
    onSuccess: async () => {
      await queryClient.invalidateQueries({ queryKey: accountKeys.sessions })
    },
  })
}

export function useRevokeOtherSessions() {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: revokeOtherSessions,
    onSuccess: async () => {
      await queryClient.invalidateQueries({ queryKey: accountKeys.sessions })
    },
  })
}
