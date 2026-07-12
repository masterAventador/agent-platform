import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'

import { getCurrentUser, login, logout, register } from './auth'

export const currentUserQueryKey = ['auth', 'current-user'] as const

export function useCurrentUser() {
  return useQuery({
    queryKey: currentUserQueryKey,
    queryFn: getCurrentUser,
    retry: false,
  })
}

export function useLogin() {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: login,
    onSuccess: (user) => queryClient.setQueryData(currentUserQueryKey, user),
  })
}

export function useRegister() {
  return useMutation({ mutationFn: register })
}

export function useLogout() {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: logout,
    onSuccess: () => queryClient.removeQueries({ queryKey: currentUserQueryKey }),
  })
}
