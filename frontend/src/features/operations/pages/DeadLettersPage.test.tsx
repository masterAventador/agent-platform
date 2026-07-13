import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter } from 'react-router-dom'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import { useReplayRunDeadLetter, useRunDeadLetters } from '../api/queries'
import { DeadLettersPage } from './DeadLettersPage'


vi.mock('../api/queries', () => ({
  useRunDeadLetters: vi.fn(),
  useReplayRunDeadLetter: vi.fn(),
}))

const mutateAsync = vi.fn()
const reset = vi.fn()
const refetch = vi.fn()

const valid = {
  id: '20000000-0000-4000-8000-000000000020',
  original_command_id: '40000000-0000-4000-8000-000000000040',
  original_run_id: '30000000-0000-4000-8000-000000000030',
  action: 'start',
  attempts: 5,
  error_type: 'delivery_processing_failed',
  is_malformed: false,
  raw_fields_summary: {
    known_field_keys: [],
    unknown_fields: [],
    field_count: 0,
    total_bytes: 0,
    sha256: null,
  },
  failed_at: '2026-07-13T08:00:00Z',
  replayed_run_id: null,
  replayed_command_id: null,
  replayed_at: null,
  settled_run_id: '30000000-0000-4000-8000-000000000030',
  mirrored_at: '2026-07-13T08:01:00Z',
}

const malformed = {
  ...valid,
  id: '20000000-0000-4000-8000-000000000021',
  original_command_id: null,
  original_run_id: null,
  action: null,
  is_malformed: true,
  error_type: 'malformed_queue_message',
  settled_run_id: null,
}

describe('DeadLettersPage', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    vi.mocked(useRunDeadLetters).mockReturnValue({
      data: [valid, malformed],
      isPending: false,
      isError: false,
      error: null,
      refetch,
    } as unknown as ReturnType<typeof useRunDeadLetters>)
    vi.mocked(useReplayRunDeadLetter).mockReturnValue({
      mutateAsync,
      reset,
      isPending: false,
      isError: false,
      error: null,
    } as unknown as ReturnType<typeof useReplayRunDeadLetter>)
  })

  it('展示死信状态并禁止 malformed 重放', () => {
    render(<MemoryRouter><DeadLettersPage /></MemoryRouter>)

    expect(screen.getByRole('heading', { name: '死信管理' })).toBeInTheDocument()
    expect(document.querySelectorAll('time[datetime="2026-07-13T08:00:00Z"]')).toHaveLength(2)
    expect(screen.getByRole('link', { name: '查看原任务' })).toHaveAttribute(
      'href',
      '/runs/30000000-0000-4000-8000-000000000030',
    )
    expect(screen.getByText('delivery_processing_failed')).toBeInTheDocument()
    expect(screen.getByText('格式异常')).toBeInTheDocument()
    expect(screen.getAllByRole('button', { name: '重放任务' })[1]).toBeDisabled()
  })

  it('二次确认合法死信并在成功后显示新任务链接', async () => {
    const user = userEvent.setup()
    mutateAsync.mockResolvedValue({
      run_id: '50000000-0000-4000-8000-000000000050',
      command_id: '60000000-0000-4000-8000-000000000060',
    })
    render(<MemoryRouter><DeadLettersPage /></MemoryRouter>)

    await user.click(screen.getAllByRole('button', { name: '重放任务' })[0])
    const dialog = screen.getByRole('dialog', { name: '确认重放任务' })
    expect(dialog).toBeInTheDocument()
    expect(dialog).toHaveTextContent('死信标识')
    expect(dialog).toHaveTextContent('20000000')
    expect(dialog).toHaveTextContent('原任务')
    expect(dialog).toHaveTextContent('30000000')
    await user.click(screen.getByRole('button', { name: '确认重放' }))

    expect(mutateAsync).toHaveBeenCalledTimes(1)
    expect(mutateAsync).toHaveBeenCalledWith(valid.id)
    expect(await screen.findByRole('link', { name: '查看新任务' })).toHaveAttribute(
      'href',
      '/runs/50000000-0000-4000-8000-000000000050',
    )
  })

  it('重放请求进行中时禁用所有重放入口以避免重复提交', () => {
    vi.mocked(useReplayRunDeadLetter).mockReturnValue({
      mutateAsync,
      reset,
      isPending: true,
      isError: false,
      error: null,
    } as unknown as ReturnType<typeof useReplayRunDeadLetter>)
    render(<MemoryRouter><DeadLettersPage /></MemoryRouter>)

    expect(screen.getAllByRole('button', { name: '重放任务' })[0]).toBeDisabled()
  })

  it('列表加载中提供可访问名称', () => {
    vi.mocked(useRunDeadLetters).mockReturnValue({
      data: undefined,
      isPending: true,
      isError: false,
      error: null,
      refetch,
    } as unknown as ReturnType<typeof useRunDeadLetters>)
    render(<MemoryRouter><DeadLettersPage /></MemoryRouter>)

    expect(screen.getByLabelText('正在加载死信任务')).toBeInTheDocument()
  })

  it('列表失败时不伪装成空列表并允许重试', async () => {
    const user = userEvent.setup()
    refetch.mockResolvedValue(undefined)
    vi.mocked(useRunDeadLetters).mockReturnValue({
      data: undefined,
      isPending: false,
      isError: true,
      error: new Error('failed'),
      refetch,
    } as unknown as ReturnType<typeof useRunDeadLetters>)
    render(<MemoryRouter><DeadLettersPage /></MemoryRouter>)

    expect(screen.getByText('死信列表加载失败')).toBeInTheDocument()
    expect(screen.queryByText('当前没有死信任务')).not.toBeInTheDocument()
    await user.click(screen.getByRole('button', { name: '重新加载' }))
    expect(refetch).toHaveBeenCalledTimes(1)
  })
})
