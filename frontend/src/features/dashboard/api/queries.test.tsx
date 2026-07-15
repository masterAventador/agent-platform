import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { renderHook, waitFor } from '@testing-library/react'
import type { ReactNode } from 'react'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import { useWorkspaceStore } from '../../workspaces/store'
import { getWorkbenchSummary } from './workbench'
import { useWorkbenchSummary, workbenchKeys } from './queries'


vi.mock('./workbench', () => ({
  getWorkbenchSummary: vi.fn(),
}))

const tenantId = '00000000-0000-4000-8000-000000000010'
const emptySummary = {
  employees: { total: 0, draft: 0, published: 0 },
  runs: {
    total: 0,
    queued: 0,
    running: 0,
    waiting_for_input: 0,
    waiting_for_approval: 0,
    completed: 0,
    failed: 0,
    cancelled: 0,
  },
}
const updatedSummary = {
  ...emptySummary,
  employees: { total: 1, draft: 0, published: 1 },
  runs: { ...emptySummary.runs, total: 1, completed: 1 },
}

describe('workbench tenant query', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    useWorkspaceStore.setState({ activeWorkspaceId: tenantId, reconciledUserId: 'user-1' })
  })

  it('每次回到工作台都刷新聚合数据而不展示突变前的缓存', async () => {
    const queryClient = new QueryClient({
      defaultOptions: { queries: { retry: false, staleTime: Number.POSITIVE_INFINITY } },
    })
    queryClient.setQueryData(workbenchKeys.summary(tenantId), emptySummary)
    vi.mocked(getWorkbenchSummary).mockResolvedValue(updatedSummary)
    const wrapper = ({ children }: { children: ReactNode }) => (
      <QueryClientProvider client={queryClient}>{children}</QueryClientProvider>
    )

    const { result } = renderHook(() => useWorkbenchSummary(), { wrapper })

    await waitFor(() => expect(result.current.data).toEqual(updatedSummary))
    expect(getWorkbenchSummary).toHaveBeenCalledWith(tenantId)
  })
})
