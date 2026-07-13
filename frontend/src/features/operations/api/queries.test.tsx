import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { act, renderHook, waitFor } from '@testing-library/react'
import type { PropsWithChildren } from 'react'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import { useActiveWorkspaceId } from '../../employees/api/queries'
import { listRunDeadLetters, replayRunDeadLetter } from './dead-letters'
import { runDeadLetterKeys, useReplayRunDeadLetter, useRunDeadLetters } from './queries'


vi.mock('../../employees/api/queries', () => ({ useActiveWorkspaceId: vi.fn() }))
vi.mock('./dead-letters', () => ({
  listRunDeadLetters: vi.fn(),
  replayRunDeadLetter: vi.fn(),
}))

const tenantId = '10000000-0000-4000-8000-000000000010'
const deadLetterId = '20000000-0000-4000-8000-000000000020'
const replayedRunId = '30000000-0000-4000-8000-000000000030'

describe('run dead letter queries', () => {
  let queryClient: QueryClient

  beforeEach(() => {
    vi.clearAllMocks()
    vi.mocked(useActiveWorkspaceId).mockReturnValue(tenantId)
    vi.mocked(listRunDeadLetters).mockResolvedValue([])
    vi.mocked(replayRunDeadLetter).mockResolvedValue({
      run_id: replayedRunId,
      command_id: '40000000-0000-4000-8000-000000000040',
    })
    queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  })

  const wrapper = ({ children }: PropsWithChildren) => (
    <QueryClientProvider client={queryClient}>{children}</QueryClientProvider>
  )

  it('列表 Query Key 包含租户且固定请求安全上限', async () => {
    const { result } = renderHook(() => useRunDeadLetters(), { wrapper })

    await waitFor(() => expect(result.current.isSuccess).toBe(true))
    expect(listRunDeadLetters).toHaveBeenCalledWith(tenantId, 100)
    expect(queryClient.getQueryData(runDeadLetterKeys.list(tenantId))).toEqual([])
  })

  it('重放成功后失效当前租户列表并返回同一服务端结果', async () => {
    const invalidate = vi.spyOn(queryClient, 'invalidateQueries')
    const { result } = renderHook(() => useReplayRunDeadLetter(), { wrapper })

    await act(async () => {
      await expect(result.current.mutateAsync(deadLetterId)).resolves.toMatchObject({
        run_id: replayedRunId,
      })
    })

    expect(replayRunDeadLetter).toHaveBeenCalledWith(tenantId, deadLetterId)
    expect(invalidate).toHaveBeenCalledWith({ queryKey: runDeadLetterKeys.list(tenantId) })
  })
})
