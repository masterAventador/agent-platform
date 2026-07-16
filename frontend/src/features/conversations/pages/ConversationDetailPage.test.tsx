import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter, Route, Routes } from 'react-router-dom'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import { useWorkspaceStore } from '../../workspaces/store'
import {
  useAppendConversationMessage,
  useCancelConversationRun,
  useConversation,
  useRetryConversation,
} from '../api/queries'
import { ConversationDetailPage } from './ConversationDetailPage'


vi.mock('../api/queries', () => ({
  useAppendConversationMessage: vi.fn(),
  useCancelConversationRun: vi.fn(),
  useConversation: vi.fn(),
  useRetryConversation: vi.fn(),
}))

const conversation = {
  id: 'conversation-1',
  tenant_id: 'tenant-1',
  employee_id: 'employee-1',
  created_by: 'user-1',
  title: '竞品调研',
  thread_id: 'conversation:conversation-1',
  messages: [
    {
      id: 'message-1',
      tenant_id: 'tenant-1',
      conversation_id: 'conversation-1',
      run_id: 'run-1',
      sequence: 1,
      role: 'user',
      content: '请分析竞品',
      attachment_ids: ['file-1'],
    },
    {
      id: 'message-2',
      tenant_id: 'tenant-1',
      conversation_id: 'conversation-1',
      run_id: 'run-1',
      sequence: 2,
      role: 'assistant',
      content: '这是初步结论',
      attachment_ids: [],
    },
  ],
  runs: [
    {
      id: 'run-1',
      tenant_id: 'tenant-1',
      employee_id: 'employee-1',
      employee_version: 1,
      created_by: 'user-1',
      thread_id: 'conversation:conversation-1',
      conversation_id: 'conversation-1',
      input: { message: '请分析竞品' },
      status: 'failed',
      error_code: 'model_timeout',
      error_message: null,
    },
  ],
}

const activeRun = {
  id: 'run-2',
  tenant_id: 'tenant-1',
  employee_id: 'employee-1',
  employee_version: 1,
  created_by: 'user-1',
  thread_id: 'conversation:conversation-1',
  conversation_id: 'conversation-1',
  input: { message: '继续补充' },
  status: 'running',
  error_code: null,
  error_message: null,
}

function renderPage({
  currentUserId = 'user-1',
  canManageRuns = false,
}: { currentUserId?: string; canManageRuns?: boolean } = {}) {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return render(
    <QueryClientProvider client={queryClient}>
      <MemoryRouter initialEntries={['/conversations/conversation-1']}>
        <Routes>
          <Route
            path="/conversations/:conversationId"
            element={(
              <ConversationDetailPage
                currentUserId={currentUserId}
                canManageRuns={canManageRuns}
              />
            )}
          />
        </Routes>
      </MemoryRouter>
    </QueryClientProvider>,
  )
}

describe('ConversationDetailPage', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    useWorkspaceStore.setState({ activeWorkspaceId: 'tenant-1', reconciledUserId: 'user-1' })
    vi.mocked(useConversation).mockReturnValue({ data: conversation, isPending: false } as never)
    vi.mocked(useAppendConversationMessage).mockReturnValue({
      mutate: vi.fn(),
      isPending: false,
    } as never)
    vi.mocked(useRetryConversation).mockReturnValue({
      mutate: vi.fn(),
      isPending: false,
    } as never)
    vi.mocked(useCancelConversationRun).mockReturnValue({
      mutate: vi.fn(),
      isPending: false,
      isSuccess: false,
    } as never)
  })

  it('renders the durable message timeline including attachment ids and failed run retry', async () => {
    const retry = vi.fn()
    vi.mocked(useRetryConversation).mockReturnValue({ mutate: retry, isPending: false } as never)
    const user = userEvent.setup()

    renderPage()

    expect(screen.getByRole('heading', { name: '竞品调研' })).toBeInTheDocument()
    expect(screen.getByText('请分析竞品')).toBeInTheDocument()
    expect(screen.getByText('这是初步结论')).toBeInTheDocument()
    expect(screen.getByText('附件 file-1')).toBeInTheDocument()
    expect(screen.getByText('失败：model_timeout')).toBeInTheDocument()

    await user.click(screen.getByRole('button', { name: '重试失败任务' }))
    expect(retry).toHaveBeenCalledWith('run-1')
  })

  it('submits appended input as real message content', async () => {
    const append = vi.fn()
    vi.mocked(useAppendConversationMessage).mockReturnValue({
      mutate: append,
      isPending: false,
    } as never)
    const user = userEvent.setup()

    renderPage()

    await user.type(screen.getByLabelText('追加消息'), '请继续补充风险')
    await user.click(screen.getByRole('button', { name: '发送' }))

    expect(append).toHaveBeenCalledWith({
      content: '请继续补充风险',
      attachmentIds: [],
      dispatch: true,
    })
  })

  it('links every related run to its run detail page', () => {
    renderPage()

    const link = screen.getByRole('link', { name: '任务详情' })
    expect(link).toHaveAttribute('href', '/runs/run-1')
  })

  it('lets the run creator cancel an active related run from the conversation', async () => {
    const cancel = vi.fn()
    vi.mocked(useCancelConversationRun).mockReturnValue({
      mutate: cancel,
      isPending: false,
      isSuccess: false,
    } as never)
    vi.mocked(useConversation).mockReturnValue({
      data: { ...conversation, runs: [activeRun] },
      isPending: false,
    } as never)
    const user = userEvent.setup()

    renderPage()

    await user.click(screen.getByRole('button', { name: '取消任务' }))
    expect(cancel).toHaveBeenCalledTimes(1)
  })

  it('hides the cancel action from users who are not the run creator', () => {
    vi.mocked(useConversation).mockReturnValue({
      data: { ...conversation, runs: [{ ...activeRun, created_by: 'someone-else' }] },
      isPending: false,
    } as never)

    renderPage()

    expect(screen.queryByRole('button', { name: '取消任务' })).not.toBeInTheDocument()
  })

  it('shows the cancel action to run managers even for runs created by others', () => {
    vi.mocked(useConversation).mockReturnValue({
      data: { ...conversation, runs: [{ ...activeRun, created_by: 'someone-else' }] },
      isPending: false,
    } as never)

    renderPage({ canManageRuns: true })

    expect(screen.getByRole('button', { name: '取消任务' })).toBeInTheDocument()
  })

  it('does not offer cancel for terminal runs', () => {
    renderPage()

    expect(screen.queryByRole('button', { name: '取消任务' })).not.toBeInTheDocument()
  })
})
