import { render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter } from 'react-router-dom'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import { useEmployees } from '../../employees/api/queries'
import type { Employee } from '../../employees/api/employees'
import {
  useCreateScheduledTask,
  useDeleteScheduledTask,
  usePauseScheduledTask,
  useResumeScheduledTask,
  useScheduledTasks,
  useUpdateScheduledTask,
} from '../api/queries'
import type { ScheduledTask } from '../api/scheduled-tasks'
import { ScheduledTasksPage } from './ScheduledTasksPage'


vi.mock('../api/queries', () => ({
  useScheduledTasks: vi.fn(),
  useCreateScheduledTask: vi.fn(),
  useUpdateScheduledTask: vi.fn(),
  usePauseScheduledTask: vi.fn(),
  useResumeScheduledTask: vi.fn(),
  useDeleteScheduledTask: vi.fn(),
}))
vi.mock('../../employees/api/queries', () => ({ useEmployees: vi.fn() }))

const employeeId = '30000000-0000-4000-8000-000000000030'

function employeeFixture(overrides: Partial<Employee> = {}): Employee {
  return {
    id: employeeId,
    tenant_id: '10000000-0000-4000-8000-000000000010',
    name: '巡检专员',
    status: 'published',
    published_version: 1,
    definition: {
      name: '巡检专员',
      role_description: '巡检',
      visibility: 'tenant',
      work_mode: 'autonomous',
      system_prompt: '巡检',
      model: { kind: 'gateway_alias', alias: 'general-purpose' },
      input_schema: { type: 'object' },
      output_schema: {},
      capabilities: {
        conversation: true,
        scheduled_tasks: true,
        file_upload: false,
        memory: false,
      },
      skill_ids: [],
      tool_ids: [],
      knowledge_base_ids: [],
      knowledge_retrieval: {
        page_size: 5,
        similarity_threshold: 0.2,
        vector_similarity_weight: 0.3,
        top_k: 1024,
        keyword: false,
        rerank_id: null,
        metadata_condition: null,
      },
      approval_policy: {},
      release_strategy: { mode: 'all' },
    },
    ...overrides,
  }
}

const cronTask: ScheduledTask = {
  id: '20000000-0000-4000-8000-000000000020',
  tenant_id: '10000000-0000-4000-8000-000000000010',
  employee_id: employeeId,
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
  last_run_at: null,
  misfire_policy: 'skip',
  concurrency_policy: 'skip',
  max_retries: 3,
  retry_backoff_seconds: 60,
  revision: 1,
  created_at: '2026-07-17T08:00:00Z',
}

function mutationStub(mutate = vi.fn()) {
  return { mutate, mutateAsync: vi.fn(), isPending: false, isError: false, error: null }
}

function renderPage() {
  return render(
    <MemoryRouter>
      <ScheduledTasksPage />
    </MemoryRouter>,
  )
}

describe('ScheduledTasksPage', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    vi.mocked(useScheduledTasks).mockReturnValue({
      data: { items: [cronTask], total: 1, limit: 50, offset: 0 },
      isPending: false,
      isError: false,
    } as never)
    vi.mocked(useEmployees).mockReturnValue({
      data: [employeeFixture()],
      isPending: false,
    } as never)
    for (const hook of [
      useCreateScheduledTask,
      useUpdateScheduledTask,
      usePauseScheduledTask,
      useResumeScheduledTask,
      useDeleteScheduledTask,
    ]) {
      vi.mocked(hook).mockReturnValue(mutationStub() as never)
    }
  })

  it('列表按任务自己的时区展示下次执行时间，而不是浏览器本地时区', () => {
    renderPage()

    const row = screen.getByRole('row', { name: /每个工作日早上九点巡检/ })
    expect(within(row).getByText('Cron 0 9 * * 1-5（Asia/Shanghai）')).toBeInTheDocument()
    // 01:00Z 在 Asia/Shanghai 是 09:00，与 Cron 的 `0 9` 自洽。
    expect(within(row).getByText('2026-07-20 09:00 (Asia/Shanghai)')).toBeInTheDocument()
    expect(within(row).getByText('启用中')).toBeInTheDocument()
  })

  it('自动暂停的任务展示机器可读原因，而不是只显示已暂停', () => {
    vi.mocked(useScheduledTasks).mockReturnValue({
      data: {
        items: [{
          ...cronTask,
          enabled: false,
          pause_reason: 'creator_permission_revoked',
          next_run_at: null,
        }],
        total: 1,
        limit: 50,
        offset: 0,
      },
      isPending: false,
      isError: false,
    } as never)
    renderPage()

    const row = screen.getByRole('row', { name: /每个工作日早上九点巡检/ })
    expect(within(row).getByText('已暂停')).toBeInTheDocument()
    expect(within(row).getByText(/创建者权限已被撤销/)).toBeInTheDocument()
    expect(within(row).getByText('—')).toBeInTheDocument()
  })

  it('创建 Cron 定时任务时提交表达式、时区与输入', async () => {
    const mutate = vi.fn()
    vi.mocked(useCreateScheduledTask).mockReturnValue(mutationStub(mutate) as never)
    const user = userEvent.setup()
    renderPage()

    await user.click(screen.getByRole('button', { name: /创建定时任务/ }))
    const dialog = screen.getByRole('dialog')
    await user.click(within(dialog).getByLabelText('数字员工'))
    await user.click(await screen.findByTitle('巡检专员'))
    await user.type(within(dialog).getByLabelText('任务名称'), '夜间巡检')
    await user.clear(within(dialog).getByLabelText('Cron 表达式'))
    await user.type(within(dialog).getByLabelText('Cron 表达式'), '0 2 * * *')
    await user.click(within(dialog).getByRole('button', { name: /创\s*建/ }))

    await waitFor(() => expect(mutate).toHaveBeenCalledTimes(1))
    expect(mutate.mock.calls[0][0]).toMatchObject({
      employee_id: employeeId,
      name: '夜间巡检',
      schedule: { kind: 'cron', cron_expression: '0 2 * * *', timezone: 'Asia/Shanghai' },
    })
  })

  it('单次预约把当地时间按所选时区换算成 UTC 后提交', async () => {
    const mutate = vi.fn()
    vi.mocked(useCreateScheduledTask).mockReturnValue(mutationStub(mutate) as never)
    const user = userEvent.setup()
    renderPage()

    await user.click(screen.getByRole('button', { name: /创建定时任务/ }))
    const dialog = screen.getByRole('dialog')
    await user.click(within(dialog).getByLabelText('数字员工'))
    await user.click(await screen.findByTitle('巡检专员'))
    await user.type(within(dialog).getByLabelText('任务名称'), '季度报告')
    await user.click(within(dialog).getByRole('radio', { name: '单次预约' }))
    await user.type(within(dialog).getByLabelText('预约时间'), '2026-08-01T10:00')
    await user.click(within(dialog).getByRole('button', { name: /创\s*建/ }))

    await waitFor(() => expect(mutate).toHaveBeenCalledTimes(1))
    expect(mutate.mock.calls[0][0].schedule).toEqual({
      kind: 'once',
      run_at: '2026-08-01T02:00:00.000Z',
      timezone: 'Asia/Shanghai',
    })
  })

  it('高频 Cron 叠加 allow 并发策略时给出可见提示', async () => {
    const user = userEvent.setup()
    renderPage()

    await user.click(screen.getByRole('button', { name: /创建定时任务/ }))
    const dialog = screen.getByRole('dialog')
    await user.clear(within(dialog).getByLabelText('Cron 表达式'))
    await user.type(within(dialog).getByLabelText('Cron 表达式'), '* * * * *')
    expect(within(dialog).queryByText(/持续消耗模型额度/)).not.toBeInTheDocument()

    await user.click(within(dialog).getByLabelText('并发策略'))
    await user.click(await screen.findByTitle('允许并发执行'))
    expect(await within(dialog).findByText(/持续消耗模型额度/)).toBeInTheDocument()
  })

  it('输入不是 JSON 对象时内联报错，不提交给后端', async () => {
    const mutate = vi.fn()
    vi.mocked(useCreateScheduledTask).mockReturnValue(mutationStub(mutate) as never)
    const user = userEvent.setup()
    renderPage()

    await user.click(screen.getByRole('button', { name: /创建定时任务/ }))
    const dialog = screen.getByRole('dialog')
    await user.click(within(dialog).getByLabelText('数字员工'))
    await user.click(await screen.findByTitle('巡检专员'))
    await user.type(within(dialog).getByLabelText('任务名称'), '坏输入')
    await user.type(within(dialog).getByLabelText('任务输入'), '不是 JSON')
    await user.click(within(dialog).getByRole('button', { name: /创\s*建/ }))

    expect(await within(dialog).findByText(/必须是合法的 JSON 对象/)).toBeInTheDocument()
    expect(mutate).not.toHaveBeenCalled()
  })

  it('创建失败时在弹窗内展示后端原因，而不是全局 Toast', async () => {
    const mutate = vi.fn((_payload, options?: { onError?: (error: unknown) => void }) => {
      options?.onError?.({
        isAxiosError: true,
        response: { status: 409, data: { detail: { code: 'scheduled_tasks_disabled' } } },
      })
    })
    vi.mocked(useCreateScheduledTask).mockReturnValue(mutationStub(mutate) as never)
    const user = userEvent.setup()
    renderPage()

    await user.click(screen.getByRole('button', { name: /创建定时任务/ }))
    const dialog = screen.getByRole('dialog')
    await user.click(within(dialog).getByLabelText('数字员工'))
    await user.click(await screen.findByTitle('巡检专员'))
    await user.type(within(dialog).getByLabelText('任务名称'), '会失败的任务')
    await user.click(within(dialog).getByRole('button', { name: /创\s*建/ }))

    expect(await within(dialog).findByText(/未开启定时任务能力/)).toBeInTheDocument()
  })

  it('只允许为已发布且开启定时任务能力的员工创建任务', async () => {
    vi.mocked(useEmployees).mockReturnValue({
      data: [
        employeeFixture(),
        employeeFixture({ id: 'draft-employee', name: '草稿员工', status: 'draft', published_version: null }),
        employeeFixture({
          id: 'no-capability',
          name: '未开启定时任务的员工',
          definition: {
            ...employeeFixture().definition,
            capabilities: {
              conversation: true,
              scheduled_tasks: false,
              file_upload: false,
              memory: false,
            },
          },
        }),
      ],
      isPending: false,
    } as never)
    const user = userEvent.setup()
    renderPage()

    await user.click(screen.getByRole('button', { name: /创建定时任务/ }))
    await user.click(within(screen.getByRole('dialog')).getByLabelText('数字员工'))

    expect(await screen.findByTitle('巡检专员')).toBeInTheDocument()
    expect(screen.queryByTitle('草稿员工')).not.toBeInTheDocument()
    expect(screen.queryByTitle('未开启定时任务的员工')).not.toBeInTheDocument()
  })

  it('暂停启用中的任务，并对已暂停的任务提供恢复', async () => {
    const pause = vi.fn()
    vi.mocked(usePauseScheduledTask).mockReturnValue(mutationStub(pause) as never)
    const user = userEvent.setup()
    const { unmount } = renderPage()

    await user.click(screen.getByRole('button', { name: /暂\s*停/ }))
    expect(pause).toHaveBeenCalledWith(cronTask.id, expect.anything())
    unmount()

    const resume = vi.fn()
    vi.mocked(useResumeScheduledTask).mockReturnValue(mutationStub(resume) as never)
    vi.mocked(useScheduledTasks).mockReturnValue({
      data: { items: [{ ...cronTask, enabled: false }], total: 1, limit: 50, offset: 0 },
      isPending: false,
      isError: false,
    } as never)
    renderPage()

    await user.click(screen.getByRole('button', { name: /恢\s*复/ }))
    expect(resume).toHaveBeenCalledWith(cronTask.id, expect.anything())
  })

  it('编辑任务时回显既有调度并按 PATCH 契约提交', async () => {
    const mutate = vi.fn()
    vi.mocked(useUpdateScheduledTask).mockReturnValue(mutationStub(mutate) as never)
    const user = userEvent.setup()
    renderPage()

    await user.click(screen.getByRole('button', { name: /编\s*辑/ }))
    const dialog = screen.getByRole('dialog')
    expect(within(dialog).getByLabelText('Cron 表达式')).toHaveValue('0 9 * * 1-5')
    expect(within(dialog).getByLabelText('任务名称')).toHaveValue('每个工作日早上九点巡检')

    await user.clear(within(dialog).getByLabelText('任务名称'))
    await user.type(within(dialog).getByLabelText('任务名称'), '改名后的巡检')
    await user.click(within(dialog).getByRole('button', { name: /保\s*存/ }))

    await waitFor(() => expect(mutate).toHaveBeenCalledTimes(1))
    const payload = mutate.mock.calls[0][0]
    expect(payload).toMatchObject({ name: '改名后的巡检' })
    // 运行态字段归调度器所有，编辑不得提交它们。
    expect(payload).not.toHaveProperty('enabled')
    expect(payload).not.toHaveProperty('next_run_at')
    expect(payload).not.toHaveProperty('revision')
  })

  it('编辑单次预约时把 UTC 回显为所选时区的当地时间，往返不漂移', async () => {
    const mutate = vi.fn()
    vi.mocked(useUpdateScheduledTask).mockReturnValue(mutationStub(mutate) as never)
    vi.mocked(useScheduledTasks).mockReturnValue({
      data: {
        items: [{
          ...cronTask,
          name: '季度报告单次预约',
          schedule: {
            kind: 'once' as const,
            timezone: 'Asia/Shanghai',
            cron_expression: null,
            run_at: '2026-08-01T02:00:00Z',
          },
        }],
        total: 1,
        limit: 50,
        offset: 0,
      },
      isPending: false,
      isError: false,
    } as never)
    const user = userEvent.setup()
    renderPage()

    await user.click(screen.getByRole('button', { name: /编\s*辑/ }))
    const dialog = screen.getByRole('dialog')
    // 02:00Z 在 Asia/Shanghai 是 10:00；datetime-local 只接受当地时间格式。
    expect(within(dialog).getByLabelText('预约时间')).toHaveValue('2026-08-01T10:00')

    await user.click(within(dialog).getByRole('button', { name: /保\s*存/ }))
    await waitFor(() => expect(mutate).toHaveBeenCalledTimes(1))
    // 未改动预约时间时，往返换算必须回到原来的 UTC 瞬时。
    expect(mutate.mock.calls[0][0].schedule).toEqual({
      kind: 'once',
      run_at: '2026-08-01T02:00:00.000Z',
      timezone: 'Asia/Shanghai',
    })
  })

  it('加载中、空列表与加载失败各有明确表达', async () => {
    vi.mocked(useScheduledTasks).mockReturnValue({
      data: undefined, isPending: true, isError: false,
    } as never)
    const { unmount } = renderPage()
    expect(screen.getByLabelText('正在加载定时任务')).toBeInTheDocument()
    unmount()

    vi.mocked(useScheduledTasks).mockReturnValue({
      data: { items: [], total: 0, limit: 50, offset: 0 }, isPending: false, isError: false,
    } as never)
    const empty = renderPage()
    expect(screen.getByText('还没有定时任务')).toBeInTheDocument()
    empty.unmount()

    const refetch = vi.fn()
    vi.mocked(useScheduledTasks).mockReturnValue({
      data: undefined, isPending: false, isError: true, refetch,
    } as never)
    renderPage()
    expect(screen.getByText('定时任务加载失败')).toBeInTheDocument()
    await userEvent.setup().click(screen.getByRole('button', { name: /重新加载/ }))
    expect(refetch).toHaveBeenCalled()
  })
})
