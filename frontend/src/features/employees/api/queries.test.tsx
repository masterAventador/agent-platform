import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { renderHook, waitFor } from '@testing-library/react'
import type { ReactNode } from 'react'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import { useWorkspaceStore } from '../../workspaces/store'
import { listEmployees } from './employees'
import { employeeKeys, useEmployees } from './queries'


vi.mock('./employees', () => ({
  listEmployees: vi.fn(),
}))

const ownerId = '00000000-0000-4000-8000-000000000010'
const adminId = '00000000-0000-4000-8000-000000000020'

describe('employee tenant queries', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    useWorkspaceStore.setState({ activeWorkspaceId: ownerId, reconciledUserId: 'user-1' })
  })

  it('query key、请求 header 参数与 active workspace 同步且不复用旧租户数据', async () => {
    vi.mocked(listEmployees)
      .mockResolvedValueOnce([{ id: 'owner-employee' }] as never)
      .mockResolvedValueOnce([{ id: 'admin-employee' }] as never)
    const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } })
    const wrapper = ({ children }: { children: ReactNode }) => (
      <QueryClientProvider client={queryClient}>{children}</QueryClientProvider>
    )
    const { result } = renderHook(() => useEmployees(), { wrapper })
    await waitFor(() => expect(result.current.data).toEqual([{ id: 'owner-employee' }]))
    expect(listEmployees).toHaveBeenLastCalledWith(ownerId)
    expect(queryClient.getQueryData(employeeKeys.all(ownerId))).toEqual([
      { id: 'owner-employee' },
    ])

    useWorkspaceStore.setState({ activeWorkspaceId: adminId })
    await waitFor(() => expect(result.current.data).toEqual([{ id: 'admin-employee' }]))

    expect(listEmployees).toHaveBeenLastCalledWith(adminId)
    expect(queryClient.getQueryData(employeeKeys.all(adminId))).toEqual([
      { id: 'admin-employee' },
    ])
  })
})
