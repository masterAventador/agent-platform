import { render, screen, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter, Route, Routes } from 'react-router-dom'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import { useScheduledTask, useScheduledTaskExecutions } from '../api/queries'
import type { ScheduledTask, ScheduledTaskExecution } from '../api/scheduled-tasks'
import { ScheduledTaskDetailPage } from './ScheduledTaskDetailPage'


vi.mock('../api/queries', () => ({
  useScheduledTask: vi.fn(),
  useScheduledTaskExecutions: vi.fn(),
}))

const taskId = '20000000-0000-4000-8000-000000000020'
const runId = '60000000-0000-4000-8000-000000000060'

const task: ScheduledTask = {
  id: taskId,
  tenant_id: '10000000-0000-4000-8000-000000000010',
  employee_id: '30000000-0000-4000-8000-000000000030',
  created_by: '40000000-0000-4000-8000-000000000040',
  name: '每个工作日早上九点巡检',
  schedule: {
    kind: 'cron',
    timezone: 'Asia/Shanghai',
    cron_expression: '0 9 * * 1-5',
    run_at: null,
  },
  input: { task: '巡检' },
  enabled: true,
  pause_reason: null,
  next_run_at: '2026-07-20T01:00:00Z',
  last_run_at: '2026-07-17T01:00:00Z',
  misfire_policy: 'skip',
  concurrency_policy: 'skip',
  max_retries: 3,
  retry_backoff_seconds: 60,
  revision: 1,
  created_at: '2026-07-17T08:00:00Z',
}

function execution(overrides: Partial<ScheduledTaskExecution> = {}): ScheduledTaskExecution {
  return {
    id: '50000000-0000-4000-8000-000000000050',
    scheduled_task_id: taskId,
    scheduled_for: '2026-07-17T01:00:00Z',
    status: 'succeeded',
    attempts: 1,
    run_id: runId,
    skip_reason: null,
    error_message: null,
    next_attempt_at: null,
    created_at: '2026-07-17T01:00:00Z',
    updated_at: '2026-07-17T01:00:05Z',
    ...overrides,
  }
}

function renderPage() {
  return render(
    <MemoryRouter initialEntries={[`/scheduled-tasks/${taskId}`]}>
      <Routes>
        <Route path="/scheduled-tasks/:taskId" element={<ScheduledTaskDetailPage />} />
      </Routes>
    </MemoryRouter>,
  )
}

describe('ScheduledTaskDetailPage', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    vi.mocked(useScheduledTask).mockReturnValue({
      data: task, isPending: false, isError: false, error: null,
    } as never)
    vi.mocked(useScheduledTaskExecutions).mockReturnValue({
      data: { items: [execution()], total: 1, limit: 50, offset: 0 },
      isPending: false,
      isError: false,
    } as never)
  })

  it('概览按任务时区展示下次与上次执行时间', () => {
    renderPage()

    expect(screen.getByRole('heading', { name: '每个工作日早上九点巡检' })).toBeInTheDocument()
    const overview = within(screen.getByRole('region', { name: '任务概览' }))
    expect(overview.getByText('2026-07-20 09:00 (Asia/Shanghai)')).toBeInTheDocument()
    expect(overview.getByText('2026-07-17 09:00 (Asia/Shanghai)')).toBeInTheDocument()
  })

  it('执行记录按任务时区展示触发时间并链接到真实任务', () => {
    renderPage()

    const row = within(screen.getByRole('region', { name: '执行记录' }))
      .getByRole('row', { name: /2026-07-17 09:00/ })
    expect(within(row).getByText('成功')).toBeInTheDocument()
    expect(within(row).getByRole('link', { name: /查看任务/ }))
      .toHaveAttribute('href', `/runs/${runId}`)
  })

  it('跳过的执行展示可读原因，而不是裸机器码', () => {
    vi.mocked(useScheduledTaskExecutions).mockReturnValue({
      data: {
        items: [execution({
          status: 'skipped',
          skip_reason: 'concurrency_skipped',
          run_id: null,
        })],
        total: 1,
        limit: 50,
        offset: 0,
      },
      isPending: false,
      isError: false,
    } as never)
    renderPage()

    const row = within(screen.getByRole('region', { name: '执行记录' }))
      .getByRole('row', { name: /2026-07-17 09:00/ })
    expect(within(row).getByText('已跳过')).toBeInTheDocument()
    expect(within(row).getByText(/上一轮仍在执行/)).toBeInTheDocument()
    expect(within(row).queryByRole('link', { name: /查看任务/ })).not.toBeInTheDocument()
  })

  it('失败的执行展示错误信息与重试时间', () => {
    vi.mocked(useScheduledTaskExecutions).mockReturnValue({
      data: {
        items: [execution({
          status: 'retry_waiting',
          attempts: 2,
          error_message: '模型网关超时',
          next_attempt_at: '2026-07-17T01:05:00Z',
        })],
        total: 1,
        limit: 50,
        offset: 0,
      },
      isPending: false,
      isError: false,
    } as never)
    renderPage()

    const row = within(screen.getByRole('region', { name: '执行记录' }))
      .getByRole('row', { name: /2026-07-17 09:00/ })
    expect(within(row).getByText('等待重试')).toBeInTheDocument()
    expect(within(row).getByText('模型网关超时')).toBeInTheDocument()
    expect(within(row).getByText(/2026-07-17 09:05 \(Asia\/Shanghai\)/)).toBeInTheDocument()
  })

  it('无权访问或不存在的任务展示明确提示，不暴露原始错误', () => {
    vi.mocked(useScheduledTask).mockReturnValue({
      data: undefined,
      isPending: false,
      isError: true,
      error: {
        isAxiosError: true,
        response: { status: 404, data: { detail: { code: 'resource_not_found' } } },
      },
    } as never)
    renderPage()

    expect(screen.getByText(/定时任务不存在或你无权访问/)).toBeInTheDocument()
  })

  it('还没有执行记录时给出空状态而不是空白表格', () => {
    vi.mocked(useScheduledTaskExecutions).mockReturnValue({
      data: { items: [], total: 0, limit: 50, offset: 0 },
      isPending: false,
      isError: false,
    } as never)
    renderPage()

    expect(screen.getByText('还没有执行记录')).toBeInTheDocument()
  })

  it('执行记录加载失败时可重新加载', async () => {
    const refetch = vi.fn()
    vi.mocked(useScheduledTaskExecutions).mockReturnValue({
      data: undefined, isPending: false, isError: true, refetch,
    } as never)
    renderPage()

    expect(screen.getByText('执行记录加载失败')).toBeInTheDocument()
    await userEvent.setup().click(screen.getByRole('button', { name: /重新加载/ }))
    expect(refetch).toHaveBeenCalled()
  })
})
