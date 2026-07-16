import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { render, screen, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter } from 'react-router-dom'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import {
  useCreateMemory,
  useDeleteMemory,
  useMemories,
  useUpdateMemory,
} from '../api/queries'
import { MemoriesPage } from './MemoriesPage'


vi.mock('../api/queries', () => ({
  useCreateMemory: vi.fn(),
  useDeleteMemory: vi.fn(),
  useMemories: vi.fn(),
  useUpdateMemory: vi.fn(),
}))

const memories = [
  {
    id: 'memory-1',
    tenant_id: 'tenant-1',
    scope: 'user' as const,
    scope_ref: 'user-1',
    content: '偏好中文邮件签名',
    source: 'run' as const,
    source_ref: 'run-1',
    confidence: 1,
    status: 'active' as const,
    expired: false,
    expires_at: null,
    created_by: 'user-1',
    created_at: '2026-07-17T08:00:00Z',
    updated_at: '2026-07-17T08:00:00Z',
  },
  {
    id: 'memory-2',
    tenant_id: 'tenant-1',
    scope: 'tenant' as const,
    scope_ref: 'tenant-1',
    content: '企业统一使用北京时区',
    source: 'manual' as const,
    source_ref: null,
    confidence: 1,
    status: 'disabled' as const,
    expired: false,
    expires_at: null,
    created_by: 'user-1',
    created_at: '2026-07-17T08:00:00Z',
    updated_at: '2026-07-17T08:00:00Z',
  },
]

function renderPage() {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return render(
    <QueryClientProvider client={queryClient}>
      <MemoryRouter>
        <MemoriesPage />
      </MemoryRouter>
    </QueryClientProvider>,
  )
}

describe('MemoriesPage', () => {
  const updateMutate = vi.fn()
  const deleteMutate = vi.fn()
  const createMutate = vi.fn()

  beforeEach(() => {
    vi.clearAllMocks()
    vi.mocked(useMemories).mockReturnValue({
      data: memories,
      isPending: false,
      isError: false,
    } as never)
    vi.mocked(useCreateMemory).mockReturnValue({
      mutateAsync: createMutate,
      isPending: false,
      isError: false,
      error: null,
    } as never)
    vi.mocked(useUpdateMemory).mockReturnValue({
      mutateAsync: updateMutate,
      isPending: false,
      isError: false,
      error: null,
    } as never)
    vi.mocked(useDeleteMemory).mockReturnValue({
      mutateAsync: deleteMutate,
      isPending: false,
      isError: false,
      error: null,
    } as never)
  })

  it('renders memories with scope, source and status', () => {
    renderPage()

    expect(screen.getByText('偏好中文邮件签名')).toBeInTheDocument()
    expect(screen.getByText('企业统一使用北京时区')).toBeInTheDocument()
    expect(screen.getByText('用户')).toBeInTheDocument()
    expect(screen.getByText('企业')).toBeInTheDocument()
    expect(screen.getByText('已禁用')).toBeInTheDocument()
  })

  it('corrects a memory through the edit dialog', async () => {
    const user = userEvent.setup()
    renderPage()

    const row = screen.getByText('偏好中文邮件签名').closest('tr')
    expect(row).not.toBeNull()
    await user.click(within(row!).getByRole('button', { name: /纠\s*正/ }))
    const textbox = await screen.findByLabelText('记忆内容')
    await user.clear(textbox)
    await user.type(textbox, '偏好英文邮件签名')
    await user.click(screen.getByRole('button', { name: /保\s*存/ }))

    expect(updateMutate).toHaveBeenCalledWith({
      memoryId: 'memory-1',
      input: { content: '偏好英文邮件签名' },
    })
  })

  it('disables, enables and deletes memories', async () => {
    const user = userEvent.setup()
    renderPage()

    const activeRow = screen.getByText('偏好中文邮件签名').closest('tr')!
    await user.click(within(activeRow).getByRole('button', { name: /禁\s*用/ }))
    expect(updateMutate).toHaveBeenCalledWith({
      memoryId: 'memory-1',
      input: { status: 'disabled' },
    })

    const disabledRow = screen.getByText('企业统一使用北京时区').closest('tr')!
    await user.click(within(disabledRow).getByRole('button', { name: /启\s*用/ }))
    expect(updateMutate).toHaveBeenCalledWith({
      memoryId: 'memory-2',
      input: { status: 'active' },
    })

    await user.click(within(activeRow).getByRole('button', { name: /删\s*除/ }))
    await user.click(await screen.findByRole('button', { name: '确认删除' }))
    expect(deleteMutate).toHaveBeenCalledWith('memory-1')
  })

  it('creates a manual memory through the create dialog', async () => {
    const user = userEvent.setup()
    renderPage()

    await user.click(screen.getByRole('button', { name: '新增记忆' }))
    await user.type(await screen.findByLabelText('记忆内容'), '企业级新规范')
    await user.click(screen.getByRole('button', { name: /保\s*存/ }))

    expect(createMutate).toHaveBeenCalledWith({ scope: 'user', content: '企业级新规范' })
  })
})
