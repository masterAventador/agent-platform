import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter, Route, Routes } from 'react-router-dom'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import { useWorkspaceStore } from '../../workspaces/store'
import {
  useAppendConversationMessage,
  useConversation,
  useRetryConversation,
} from '../api/queries'
import { ConversationDetailPage } from './ConversationDetailPage'


vi.mock('../api/queries', () => ({
  useAppendConversationMessage: vi.fn(),
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
      thread_id: 'conversation:conversation-1',
      conversation_id: 'conversation-1',
      input: { message: '请分析竞品' },
      status: 'failed',
      error_code: 'model_timeout',
      error_message: null,
    },
  ],
}

function renderPage() {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return render(
    <QueryClientProvider client={queryClient}>
      <MemoryRouter initialEntries={['/conversations/conversation-1']}>
        <Routes>
          <Route path="/conversations/:conversationId" element={<ConversationDetailPage />} />
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
})
