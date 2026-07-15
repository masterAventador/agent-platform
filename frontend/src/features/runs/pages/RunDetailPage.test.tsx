import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { act, render, screen, waitFor } from '@testing-library/react'
import { MemoryRouter, Route, Routes } from 'react-router-dom'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import { useWorkspaceStore } from '../../workspaces/store'
import { getPlatformAdapter } from '../../../platform'
import {
  useArtifacts,
  useControlRun,
  useDeleteArtifact,
  useRun,
  useRunEvents,
} from '../api/queries'
import { downloadArtifact } from '../api/runs'
import { RunDetailPage } from './RunDetailPage'


vi.mock('../api/queries', async (importOriginal) => {
  const original = await importOriginal<typeof import('../api/queries')>()
  return {
    ...original,
    useArtifacts: vi.fn(),
    useControlRun: vi.fn(),
    useDeleteArtifact: vi.fn(),
    useRun: vi.fn(),
    useRunEvents: vi.fn(),
  }
})

vi.mock('../../../platform', () => ({ getPlatformAdapter: vi.fn() }))
vi.mock('../api/runs', async (importOriginal) => {
  const original = await importOriginal<typeof import('../api/runs')>()
  return { ...original, downloadArtifact: vi.fn() }
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

function renderPage(canExecuteRuns = true, canManageRuns = true) {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return render(
    <QueryClientProvider client={queryClient}>
      <MemoryRouter initialEntries={['/runs/run-1']}>
        <Routes>
          <Route
            path="/runs/:runId"
            element={(
              <RunDetailPage
                canExecuteRuns={canExecuteRuns}
                canManageRuns={canManageRuns}
              />
            )}
          />
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
    vi.mocked(useArtifacts).mockReturnValue({ data: [], isPending: false } as never)
    vi.mocked(useDeleteArtifact).mockReturnValue({ mutate: vi.fn(), isPending: false } as never)
    vi.mocked(getPlatformAdapter).mockReturnValue({ saveFile: vi.fn() } as never)
    vi.mocked(downloadArtifact).mockResolvedValue(new TextEncoder().encode('artifact content'))
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

  it('separates member run execution from owner/admin approval management', () => {
    vi.mocked(useRun).mockReturnValue({
      data: { ...runningRun, status: 'waiting_for_approval' },
      isPending: false,
    } as never)
    vi.mocked(useRunEvents).mockReturnValue({
      data: [{
        event_id: 'approval-event',
        type: 'approval.required',
        sequence: 2,
        payload: { approval_id: 'approval-1' },
      }],
      isPending: false,
    } as never)

    const view = renderPage(true, false)

    expect(screen.getByRole('button', { name: '取消任务' })).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: /批\s*准/ })).not.toBeInTheDocument()
    expect(screen.queryByRole('button', { name: /拒\s*绝/ })).not.toBeInTheDocument()

    view.unmount()
    renderPage(true, true)
    expect(screen.getByRole('button', { name: /批\s*准/ })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: /拒\s*绝/ })).toBeInTheDocument()
  })

  it('hides resume and cancel when runs.execute is absent', () => {
    renderPage(false, false)

    expect(screen.queryByRole('button', { name: '取消任务' })).not.toBeInTheDocument()
  })

  it('previews, downloads, locates and deletes persistent artifacts', async () => {
    const user = (await import('@testing-library/user-event')).default.setup()
    const mutate = vi.fn()
    const saveFile = vi.fn()
    vi.mocked(useArtifacts).mockReturnValue({
      data: [{
        id: 'artifact-1',
        run_id: 'run-1',
        name: 'result.txt',
        media_type: 'text/plain',
        size_bytes: 16,
        sha256: 'abc',
        created_at: '2026-07-15T00:00:00Z',
      }],
      isPending: false,
    } as never)
    vi.mocked(useDeleteArtifact).mockReturnValue({ mutate, isPending: false } as never)
    vi.mocked(getPlatformAdapter).mockReturnValue({ saveFile } as never)
    vi.mocked(useRunEvents).mockReturnValue({
      data: [{
        event_id: 'event-1',
        type: 'artifact.created',
        sequence: 2,
        payload: { artifact_id: 'artifact-1', name: 'result.txt' },
      }],
      isPending: false,
    } as never)
    const scrollIntoView = vi.fn()
    Element.prototype.scrollIntoView = scrollIntoView

    renderPage()

    expect(screen.getAllByText('result.txt')).toHaveLength(2)
    await user.click(screen.getByRole('button', { name: '预览 result.txt' }))
    expect(await screen.findByText('artifact content')).toBeInTheDocument()
    await user.click(screen.getByRole('button', { name: /关\s*闭/ }))
    await user.click(screen.getByRole('button', { name: '下载 result.txt' }))
    expect(saveFile).toHaveBeenCalledWith(expect.objectContaining({ suggestedName: 'result.txt' }))
    await user.click(screen.getByRole('button', { name: '定位 result.txt' }))
    expect(scrollIntoView).toHaveBeenCalled()
    await user.click(screen.getByRole('button', { name: '删除 result.txt' }))
    expect(mutate).toHaveBeenCalledWith('artifact-1')
  })

  it.each([
    [403, '无权访问任务'],
    [404, '任务不存在或无权访问'],
  ])('renders a controlled %s error page instead of a permanent spinner', (status, title) => {
    vi.mocked(useRun).mockReturnValue({
      data: undefined,
      isPending: false,
      isError: true,
      error: { response: { status } },
    } as never)

    renderPage()

    expect(screen.getByText(title)).toBeInTheDocument()
  })
})
