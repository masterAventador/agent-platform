import { QueryClient, QueryClientProvider, useQuery } from '@tanstack/react-query'
import { act, renderHook, waitFor } from '@testing-library/react'
import type { ReactNode } from 'react'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import { cancelSessionRequests } from '../../../api/client'
import { useWorkspaceStore } from '../../workspaces/store'
import type { CurrentUser } from './auth'
import { login, logout, register } from './auth'
import {
  currentUserQueryKey,
  useLogin,
  useLogout,
  useRegister,
} from './queries'


vi.mock('./auth', () => ({
  getCurrentUser: vi.fn(),
  login: vi.fn(),
  logout: vi.fn(),
  register: vi.fn(),
}))

vi.mock('../../../api/client', () => ({
  cancelSessionRequests: vi.fn(),
}))

const workspaceId = '00000000-0000-4000-8000-000000000010'
const workspace = {
  id: workspaceId,
  name: 'Shared workspace',
  slug: 'shared-workspace',
  role: 'owner' as const,
}
const oldUser: CurrentUser = {
  id: 'old-user',
  email: 'old@example.com',
  email_verified: true,
  workspaces: [workspace],
}
const newUser: CurrentUser = {
  id: 'new-user',
  email: 'new@example.com',
  email_verified: true,
  workspaces: [workspace],
}
const businessQueryKey = ['employees', workspaceId] as const

function wrapper(queryClient: QueryClient) {
  return function QueryWrapper({ children }: { children: ReactNode }) {
    return <QueryClientProvider client={queryClient}>{children}</QueryClientProvider>
  }
}

function seedOldSession(queryClient: QueryClient) {
  queryClient.setQueryData(currentUserQueryKey, oldUser)
  queryClient.setQueryData(businessQueryKey, [{ id: 'old-user-employee' }])
  queryClient.getMutationCache().build(queryClient, {
    mutationKey: ['employees', workspaceId, 'old-mutation'],
    mutationFn: async () => undefined,
  })
  useWorkspaceStore.getState().reconcile(oldUser)
}

describe('authentication session cache isolation', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    sessionStorage.clear()
    useWorkspaceStore.getState().clear()
    vi.mocked(login).mockResolvedValue(newUser)
    vi.mocked(register).mockResolvedValue(newUser)
    vi.mocked(logout).mockResolvedValue(undefined)
  })

  it('login clears the previous account before exposing a new user with the same workspace', async () => {
    const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } })
    seedOldSession(queryClient)
    const hook = renderHook(() => useLogin(), { wrapper: wrapper(queryClient) })

    await act(() => hook.result.current.mutateAsync({ email: newUser.email, password: 'password' }))

    expect(cancelSessionRequests).toHaveBeenCalledTimes(1)
    expect(queryClient.getQueryData(currentUserQueryKey)).toEqual(newUser)
    expect(queryClient.getQueryData(businessQueryKey)).toBeUndefined()
    expect(queryClient.getMutationCache().getAll()).toEqual([])
    expect(useWorkspaceStore.getState()).toMatchObject({
      activeWorkspaceId: undefined,
      reconciledUserId: undefined,
    })

    const pendingNewAccountRequest = vi.fn(() => new Promise<never>(() => undefined))
    const business = renderHook(() => useQuery({
      queryKey: businessQueryKey,
      queryFn: pendingNewAccountRequest,
    }), { wrapper: wrapper(queryClient) })
    expect(business.result.current.data).toBeUndefined()
    expect(pendingNewAccountRequest).toHaveBeenCalledTimes(1)
  })

  it('register clears every previous account cache before returning success', async () => {
    const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } })
    seedOldSession(queryClient)
    const hook = renderHook(() => useRegister(), { wrapper: wrapper(queryClient) })

    await act(() => hook.result.current.mutateAsync({ email: newUser.email, password: 'password' }))

    expect(cancelSessionRequests).toHaveBeenCalledTimes(1)
    expect(queryClient.getQueryCache().getAll()).toEqual([])
    expect(queryClient.getMutationCache().getAll()).toEqual([])
    expect(useWorkspaceStore.getState()).toMatchObject({
      activeWorkspaceId: undefined,
      reconciledUserId: undefined,
    })
  })

  it('logout cancels pending queries and clears all query, mutation and workspace state', async () => {
    const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } })
    seedOldSession(queryClient)
    const queryAborted = vi.fn()
    const pendingQuery = queryClient.fetchQuery({
      queryKey: ['runs', workspaceId],
      queryFn: ({ signal }) => new Promise<never>((_, reject) => {
        signal.addEventListener('abort', () => {
          queryAborted()
          reject(new DOMException('cancelled', 'AbortError'))
        })
      }),
    })
    const pendingResult = expect(pendingQuery).rejects.toBeDefined()
    await waitFor(() => expect(queryClient.isFetching()).toBe(1))
    const hook = renderHook(() => useLogout(), { wrapper: wrapper(queryClient) })

    await act(() => hook.result.current.mutateAsync())

    await pendingResult
    expect(cancelSessionRequests).toHaveBeenCalledTimes(1)
    expect(queryAborted).toHaveBeenCalledTimes(1)
    expect(queryClient.getQueryCache().getAll()).toEqual([])
    expect(queryClient.getMutationCache().getAll()).toEqual([])
    expect(useWorkspaceStore.getState()).toMatchObject({
      activeWorkspaceId: undefined,
      reconciledUserId: undefined,
    })
    expect(sessionStorage.getItem('agent-platform.active-workspace')).toBeNull()
  })
})
