import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { act, render, screen, waitFor } from '@testing-library/react'
import { MemoryRouter, Route, Routes } from 'react-router-dom'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import { useWorkspaceStore } from '../../workspaces/store'
import { useControlRun, useRun, useRunEvents } from '../api/queries'
import { RunDetailPage } from './RunDetailPage'


vi.mock('../api/queries', async (importOriginal) => {
  const original = await importOriginal<typeof import('../api/queries')>()
  return {
    ...original,
    useControlRun: vi.fn(),
    useRun: vi.fn(),
    useRunEvents: vi.fn(),
  }
})

class EventSourceStub {
  static instances: EventSourceStub[] = []

  readonly url: string
  readonly withCredentials: boolean
  readonly addEventListener = vi.fn()
  readonly close = vi.fn()

  constructor(url: string | URL, options?: EventSourceInit) {
    this.url = String(url)
    this.withCredentials = options?.withCredentials ?? false
    EventSourceStub.instances.push(this)
  }
}

const runningRun = {
  id: 'run-1',
  tenant_id: 'tenant-1',
  employee_id: 'employee-1',
  employee_version: 1,
  thread_id: 'run-1',
  input: { task: 'test' },
  status: 'running',
  error_code: null,
  error_message: null,
}

function renderPage() {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return render(
    <QueryClientProvider client={queryClient}>
      <MemoryRouter initialEntries={['/runs/run-1']}>
        <Routes>
          <Route path="/runs/:runId" element={<RunDetailPage />} />
        </Routes>
      </MemoryRouter>
    </QueryClientProvider>,
  )
}

describe('RunDetailPage tenant-scoped event stream', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    EventSourceStub.instances = []
    vi.stubGlobal('EventSource', EventSourceStub)
    useWorkspaceStore.setState({ activeWorkspaceId: 'tenant-1', reconciledUserId: 'user-1' })
    vi.mocked(useRun).mockReturnValue({ data: runningRun, isPending: false } as never)
    vi.mocked(useRunEvents).mockReturnValue({ data: [], isPending: false } as never)
    vi.mocked(useControlRun).mockReturnValue({ mutate: vi.fn(), isPending: false } as never)
  })

  it('binds the stream to the active workspace and closes the old connection on switch', async () => {
    const view = renderPage()

    await waitFor(() => expect(EventSourceStub.instances).toHaveLength(1))
    expect(EventSourceStub.instances[0]?.url).toBe(
      '/api/v1/runs/run-1/stream?tenant_id=tenant-1',
    )
    expect(EventSourceStub.instances[0]?.withCredentials).toBe(true)

    act(() => {
      useWorkspaceStore.setState({ activeWorkspaceId: 'tenant-2' })
    })

    await waitFor(() => expect(EventSourceStub.instances).toHaveLength(2))
    expect(EventSourceStub.instances[0]?.close).toHaveBeenCalledTimes(1)
    expect(EventSourceStub.instances[1]?.url).toBe(
      '/api/v1/runs/run-1/stream?tenant_id=tenant-2',
    )

    view.unmount()
    expect(EventSourceStub.instances[1]?.close).toHaveBeenCalledTimes(1)
  })

  it.each([
    {
      name: 'control response',
      control: {
        mutate: vi.fn(),
        isPending: false,
        isSuccess: true,
        variables: { action: 'cancel' },
      },
      events: [],
    },
    {
      name: 'persisted event',
      control: { mutate: vi.fn(), isPending: false, isSuccess: false },
      events: [{
        event_id: 'event-1',
        type: 'run.progress',
        sequence: 2,
        payload: { action: 'cancel_requested' },
      }],
    },
  ])('shows a non-terminal cancellation intent from $name', ({ control, events }) => {
    vi.mocked(useControlRun).mockReturnValue(control as never)
    vi.mocked(useRunEvents).mockReturnValue({ data: events, isPending: false } as never)

    renderPage()

    expect(screen.getByRole('button', { name: '取消处理中' })).toBeDisabled()
    expect(screen.getByText('执行中', { exact: true })).toBeInTheDocument()
    expect(screen.queryByText('已取消', { exact: true })).not.toBeInTheDocument()
  })
})
