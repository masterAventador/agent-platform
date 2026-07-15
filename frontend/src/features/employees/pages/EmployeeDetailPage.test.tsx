import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter, Route, Routes } from 'react-router-dom'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import type { Employee } from '../api/employees'
import { useEmployee, usePublishEmployee } from '../api/queries'
import { useCreateRun } from '../../runs/api/queries'
import { EmployeeDetailPage } from './EmployeeDetailPage'
import { getPlatformAdapter } from '../../../platform'
import { deleteUnboundFile, uploadFile } from '../../runs/api/runs'


vi.mock('../api/queries', () => ({
  useEmployee: vi.fn(),
  usePublishEmployee: vi.fn(),
}))

vi.mock('../../runs/api/queries', () => ({
  useCreateRun: vi.fn(),
}))

vi.mock('../../../platform', () => ({ getPlatformAdapter: vi.fn() }))
vi.mock('../../runs/api/runs', () => ({ deleteUnboundFile: vi.fn(), uploadFile: vi.fn() }))

const employee: Employee = {
  id: 'employee-1',
  tenant_id: 'tenant-1',
  name: '研究专员',
  status: 'draft',
  published_version: null,
  definition: {
    name: '研究专员',
    avatar_url: null,
    role_description: '负责研究任务',
    visibility: 'tenant',
    work_mode: 'autonomous',
    system_prompt: '完成用户交付的研究任务',
    model: { kind: 'gateway_alias', alias: 'general-purpose' },
    input_schema: { type: 'object' },
    output_schema: { type: 'object' },
    capabilities: { conversation: true, scheduled_tasks: false, file_upload: false },
    skill_ids: [],
    tool_ids: [],
    knowledge_base_ids: [],
    approval_policy: {},
    release_strategy: { mode: 'all' },
  },
}

function renderPage(canManageEmployees = true, canExecuteRuns = true) {
  return render(
    <MemoryRouter initialEntries={['/employees/employee-1']}>
      <Routes>
        <Route
          path="/employees/:employeeId"
          element={(
            <EmployeeDetailPage
              canManageEmployees={canManageEmployees}
              canExecuteRuns={canExecuteRuns}
            />
          )}
        />
        <Route path="/employees/:employeeId/edit" element={<div>editor</div>} />
      </Routes>
    </MemoryRouter>,
  )
}

describe('EmployeeDetailPage legacy configuration guard', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    vi.mocked(useEmployee).mockReturnValue({ data: employee, isPending: false } as never)
    vi.mocked(usePublishEmployee).mockReturnValue({
      mutate: vi.fn(),
      isPending: false,
      isError: false,
      error: null,
    } as never)
    vi.mocked(useCreateRun).mockReturnValue({
      isPending: false,
      isError: false,
      error: null,
      mutateAsync: vi.fn(),
      reset: vi.fn(),
    } as never)
    vi.mocked(getPlatformAdapter).mockReturnValue({
      selectFile: vi.fn().mockResolvedValue({ name: 'brief.txt', bytes: new Uint8Array([1, 2]) }),
    } as never)
    vi.mocked(uploadFile).mockResolvedValue({
      id: 'file-1', name: 'brief.txt', media_type: 'text/plain', size_bytes: 2, sha256: 'abc',
    })
    vi.mocked(deleteUnboundFile).mockResolvedValue({ deleted: true })
  })

  it('does not silently publish a legacy configuration', () => {
    vi.mocked(useEmployee).mockReturnValue({
      data: {
        ...employee,
        definition: { ...employee.definition, work_mode: 'hybrid' },
      },
      isPending: false,
    } as never)
    renderPage()

    expect(screen.getByText(/当前员工包含尚未开放的配置/)).toBeInTheDocument()
    expect(screen.getByRole('button', { name: '发布员工' })).toBeDisabled()
    expect(screen.getByRole('button', { name: '编辑并修正' })).toBeInTheDocument()
  })

  it('shows an actionable message when the backend rejects publishing', () => {
    vi.mocked(usePublishEmployee).mockReturnValue({
      mutate: vi.fn(),
      isPending: false,
      isError: true,
      error: {
        isAxiosError: true,
        response: {
          data: { detail: { code: 'employee_configuration_unavailable' } },
        },
      },
    } as never)
    renderPage()

    expect(screen.getByText(/切换为自主执行并关闭未接通能力后重试/)).toBeInTheDocument()
  })

  it('legacy published employee cannot start a run and has a repair entry', () => {
    vi.mocked(useEmployee).mockReturnValue({
      data: {
        ...employee,
        status: 'published',
        published_version: 1,
        definition: { ...employee.definition, work_mode: 'workflow' },
      },
      isPending: false,
    } as never)

    renderPage()

    expect(screen.queryByRole('button', { name: '发起任务' })).not.toBeInTheDocument()
    expect(screen.getByText(/已发布版本包含尚未开放的配置，当前不能发起任务/))
      .toBeInTheDocument()
    expect(screen.getByRole('button', { name: '编辑并修正' })).toBeInTheDocument()
  })

  it('member keeps run/read access but cannot edit or publish', () => {
    vi.mocked(useEmployee).mockReturnValue({
      data: { ...employee, status: 'published', published_version: 1 },
      isPending: false,
    } as never)

    renderPage(false)

    expect(screen.getByRole('button', { name: '发起任务' })).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: '编辑' })).not.toBeInTheDocument()
    expect(screen.queryByRole('button', { name: '发布员工' })).not.toBeInTheDocument()
    expect(screen.getByText('网关模型')).toBeInTheDocument()
    expect(screen.getByText('general-purpose')).toBeInTheDocument()
    expect(screen.queryByText(/LiteLLM|openai/i)).not.toBeInTheDocument()
    expect(screen.getByText('完成用户交付的研究任务')).toBeInTheDocument()
  })

  it('hides run creation when runs.execute is absent even if employee is published', () => {
    vi.mocked(useEmployee).mockReturnValue({
      data: { ...employee, status: 'published', published_version: 1 },
      isPending: false,
    } as never)

    renderPage(true, false)

    expect(screen.queryByRole('button', { name: '发起任务' })).not.toBeInTheDocument()
    expect(screen.getByRole('button', { name: /编\s*辑/ })).toBeInTheDocument()
  })

  it('selects and uploads an attachment before creating a file-enabled run', async () => {
    const user = userEvent.setup()
    const mutateAsync = vi.fn().mockResolvedValue({ id: 'run-1' })
    vi.mocked(useEmployee).mockReturnValue({
      data: {
        ...employee,
        status: 'published',
        published_version: 1,
        definition: {
          ...employee.definition,
          capabilities: { ...employee.definition.capabilities, file_upload: true },
        },
      },
      isPending: false,
    } as never)
    vi.mocked(useCreateRun).mockReturnValue({
      isPending: false, isError: false, error: null, mutateAsync, reset: vi.fn(),
    } as never)
    renderPage()

    await user.click(screen.getByRole('button', { name: '发起任务' }))
    await user.click(screen.getByRole('button', { name: '选择文件' }))
    expect(await screen.findByText('brief.txt')).toBeInTheDocument()
    await user.type(screen.getByRole('textbox', { name: '任务内容' }), '总结附件')
    await user.click(screen.getByRole('button', { name: '确认发起' }))

    await waitFor(() => expect(uploadFile).toHaveBeenCalled())
    expect(mutateAsync).toHaveBeenCalledWith({
      input: { message: '总结附件' }, attachmentIds: ['file-1'],
      idempotencyKey: expect.any(String),
    })
  })

  it('shows a controlled error when the desktop file selector fails', async () => {
    const user = userEvent.setup()
    vi.mocked(useEmployee).mockReturnValue({
      data: {
        ...employee,
        status: 'published',
        published_version: 1,
        definition: {
          ...employee.definition,
          capabilities: { ...employee.definition.capabilities, file_upload: true },
        },
      },
      isPending: false,
    } as never)
    vi.mocked(getPlatformAdapter).mockReturnValue({
      selectFile: vi.fn().mockRejectedValue(new Error('permission denied')),
    } as never)
    renderPage()

    await user.click(screen.getByRole('button', { name: '发起任务' }))
    await user.click(screen.getByRole('button', { name: '选择文件' }))

    expect(await screen.findByText(/无法读取所选文件/)).toBeInTheDocument()
  })

  it('keeps the modal open and explains an attachment upload failure', async () => {
    const user = userEvent.setup()
    const mutateAsync = vi.fn()
    vi.mocked(useEmployee).mockReturnValue({
      data: {
        ...employee,
        status: 'published',
        published_version: 1,
        definition: {
          ...employee.definition,
          capabilities: { ...employee.definition.capabilities, file_upload: true },
        },
      },
      isPending: false,
    } as never)
    vi.mocked(useCreateRun).mockReturnValue({
      isPending: false, isError: false, error: null, mutateAsync, reset: vi.fn(),
    } as never)
    vi.mocked(uploadFile).mockRejectedValue(new Error('network unavailable'))
    renderPage()

    await user.click(screen.getByRole('button', { name: '发起任务' }))
    await user.click(screen.getByRole('button', { name: '选择文件' }))
    await user.type(screen.getByRole('textbox', { name: '任务内容' }), '总结附件')
    await user.click(screen.getByRole('button', { name: '确认发起' }))

    expect(await screen.findByText(/附件上传失败/)).toBeInTheDocument()
    expect(mutateAsync).not.toHaveBeenCalled()
    expect(screen.getByRole('dialog')).toBeInTheDocument()
  })

  it('compensates an uploaded file when run creation fails', async () => {
    const user = userEvent.setup()
    const mutateAsync = vi.fn().mockRejectedValue(new Error('create failed'))
    vi.mocked(useEmployee).mockReturnValue({
      data: {
        ...employee,
        status: 'published',
        published_version: 1,
        definition: {
          ...employee.definition,
          capabilities: { ...employee.definition.capabilities, file_upload: true },
        },
      },
      isPending: false,
    } as never)
    vi.mocked(useCreateRun).mockReturnValue({
      isPending: false, isError: true, error: new Error('create failed'), mutateAsync, reset: vi.fn(),
    } as never)
    renderPage()

    await user.click(screen.getByRole('button', { name: '发起任务' }))
    await user.click(screen.getByRole('button', { name: '选择文件' }))
    await user.type(screen.getByRole('textbox', { name: '任务内容' }), '总结附件')
    await user.click(screen.getByRole('button', { name: '确认发起' }))

    await waitFor(() => expect(deleteUnboundFile).toHaveBeenCalledWith('tenant-1', 'file-1'))
  })

  it('locks synchronously before a delayed upload so double click submits only once', async () => {
    const user = userEvent.setup()
    let releaseUpload!: () => void
    vi.mocked(uploadFile).mockImplementation(() => new Promise((resolve) => {
      releaseUpload = () => resolve({
        id: 'file-1', name: 'brief.txt', media_type: 'text/plain', size_bytes: 2, sha256: 'abc',
      })
    }))
    const mutateAsync = vi.fn().mockResolvedValue({ id: 'run-1' })
    vi.mocked(useEmployee).mockReturnValue({
      data: {
        ...employee,
        status: 'published',
        published_version: 1,
        definition: {
          ...employee.definition,
          capabilities: { ...employee.definition.capabilities, file_upload: true },
        },
      },
      isPending: false,
    } as never)
    vi.mocked(useCreateRun).mockReturnValue({
      isPending: false, isError: false, error: null, mutateAsync, reset: vi.fn(),
    } as never)
    renderPage()

    await user.click(screen.getByRole('button', { name: '发起任务' }))
    await user.click(screen.getByRole('button', { name: '选择文件' }))
    await user.type(screen.getByRole('textbox', { name: '任务内容' }), '总结附件')
    const submit = screen.getByRole('button', { name: '确认发起' })
    fireEvent.click(submit)
    fireEvent.click(submit)

    expect(uploadFile).toHaveBeenCalledTimes(1)
    releaseUpload()
    await waitFor(() => expect(mutateAsync).toHaveBeenCalledTimes(1))
  })

  it('uses a new idempotency key after the task intent changes', async () => {
    const user = userEvent.setup()
    const mutateAsync = vi.fn().mockRejectedValue(new Error('create failed'))
    vi.mocked(useEmployee).mockReturnValue({
      data: { ...employee, status: 'published', published_version: 1 },
      isPending: false,
    } as never)
    vi.mocked(useCreateRun).mockReturnValue({
      isPending: false, isError: true, error: new Error('create failed'), mutateAsync, reset: vi.fn(),
    } as never)
    renderPage()

    await user.click(screen.getByRole('button', { name: '发起任务' }))
    const taskInput = screen.getByRole('textbox', { name: '任务内容' })
    await user.type(taskInput, '第一次任务')
    await user.click(screen.getByRole('button', { name: '确认发起' }))
    await waitFor(() => expect(mutateAsync).toHaveBeenCalledTimes(1))
    const firstKey = mutateAsync.mock.calls[0][0].idempotencyKey
    await waitFor(() => expect(
      screen.getByRole('button', { name: /确认发起/ }),
    ).toBeEnabled())

    await user.clear(taskInput)
    await user.type(taskInput, '第二次任务')
    await user.click(screen.getByRole('button', { name: /确认发起/ }))
    await waitFor(() => expect(mutateAsync).toHaveBeenCalledTimes(2))

    expect(mutateAsync.mock.calls[1][0].idempotencyKey).not.toBe(firstKey)
  })

  it('catches createRun 409 and renders an actionable error in the modal', async () => {
    const user = userEvent.setup()
    const mutateAsync = vi.fn().mockRejectedValue({
      isAxiosError: true,
      response: { data: { detail: { code: 'employee_configuration_unavailable' } } },
    })
    vi.mocked(useEmployee).mockReturnValue({
      data: { ...employee, status: 'published', published_version: 1 },
      isPending: false,
    } as never)
    vi.mocked(useCreateRun).mockReturnValue({
      isPending: false,
      isError: true,
      error: {
        isAxiosError: true,
        response: { data: { detail: { code: 'employee_configuration_unavailable' } } },
      },
      mutateAsync,
      reset: vi.fn(),
    } as never)
    renderPage()

    await user.click(screen.getByRole('button', { name: '发起任务' }))
    expect(screen.getByText(/切换为自主执行并关闭未接通能力后重试/)).toBeInTheDocument()
    await user.type(screen.getByRole('textbox', { name: '任务内容' }), '执行任务')
    await user.click(screen.getByRole('button', { name: '确认发起' }))

    await waitFor(() => expect(mutateAsync).toHaveBeenCalledWith({
      input: { message: '执行任务' }, attachmentIds: [],
      idempotencyKey: expect.any(String),
    }))
    expect(screen.getByRole('dialog')).toBeInTheDocument()
  })

  it('renders a controlled not-found page instead of a permanent spinner', () => {
    vi.mocked(useEmployee).mockReturnValue({
      data: undefined,
      isPending: false,
      isError: true,
      error: { response: { status: 404 } },
    } as never)

    renderPage()

    expect(screen.getByText('数字员工不存在或无权访问')).toBeInTheDocument()
    expect(screen.queryByLabelText('正在加载页面')).not.toBeInTheDocument()
  })
})
