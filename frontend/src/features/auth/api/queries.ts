import {
  type QueryClient,
  useMutation,
  useQuery,
  useQueryClient,
} from '@tanstack/react-query'

import { cancelSessionRequests } from '../../../api/client'
import { getCurrentUser, login, logout, register } from './auth'
import { useWorkspaceStore } from '../../workspaces/store'

export const currentUserQueryKey = ['auth', 'current-user'] as const

async function clearPreviousSession(queryClient: QueryClient): Promise<void> {
  cancelSessionRequests()
  const pendingCancellation = queryClient.cancelQueries()
  useWorkspaceStore.getState().clear()
  queryClient.clear()
  await pendingCancellation
}

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
    onSuccess: async (user) => {
      await clearPreviousSession(queryClient)
      queryClient.setQueryData(currentUserQueryKey, user)
    },
  })
}

export function useRegister() {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: register,
    onSuccess: async () => clearPreviousSession(queryClient),
  })
}

export function useLogout() {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: logout,
    onSuccess: async () => clearPreviousSession(queryClient),
  })
}
