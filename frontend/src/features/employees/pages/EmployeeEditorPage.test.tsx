import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter, Route, Routes } from 'react-router-dom'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import type { Employee } from '../api/employees'
import { useCreateEmployee, useEmployee, useUpdateEmployee } from '../api/queries'
import { EmployeeEditorPage } from './EmployeeEditorPage'


vi.mock('../api/queries', () => ({
  useCreateEmployee: vi.fn(),
  useEmployee: vi.fn(),
  useUpdateEmployee: vi.fn(),
}))

vi.mock('../../skills/api/queries', () => ({
  usePublishedSkills: vi.fn(() => ({ data: [], isPending: false })),
}))

vi.mock('../../tools/api/queries', () => ({
  useAvailableTools: vi.fn(() => ({ data: [], servers: [], isPending: false })),
}))

const createMutateAsync = vi.fn()
const updateMutateAsync = vi.fn()

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
    model: { provider: 'openai', name: 'gpt-5' },
    input_schema: { type: 'object' },
    output_schema: { type: 'object' },
    capabilities: {
      conversation: true,
      scheduled_tasks: false,
      file_upload: false,
    },
    skill_ids: [],
    tool_ids: [],
    knowledge_base_ids: [],
    approval_policy: {},
    release_strategy: { mode: 'all' },
  },
}

function renderEditor(path = '/employees/new') {
  return render(
    <MemoryRouter initialEntries={[path]}>
      <Routes>
        <Route path="/employees/new" element={<EmployeeEditorPage />} />
        <Route path="/employees/:employeeId/edit" element={<EmployeeEditorPage />} />
        <Route path="/employees/:employeeId" element={<div>employee detail</div>} />
      </Routes>
    </MemoryRouter>,
  )
}

function mutation(overrides: Record<string, unknown> = {}) {
  return {
    mutateAsync: createMutateAsync,
    isPending: false,
    isError: false,
    error: null,
    ...overrides,
  }
}

describe('EmployeeEditorPage configuration availability', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    vi.mocked(useEmployee).mockReturnValue({ data: undefined, isPending: false } as never)
    vi.mocked(useCreateEmployee).mockReturnValue(mutation() as never)
    vi.mocked(useUpdateEmployee).mockReturnValue({
      ...mutation(),
      mutateAsync: updateMutateAsync,
    } as never)
    createMutateAsync.mockResolvedValue(employee)
    updateMutateAsync.mockResolvedValue(employee)
  })

  it('shows unavailable modes and keeps unfinished capabilities disabled and false', async () => {
    const user = userEvent.setup()
    renderEditor()

    expect(screen.getByRole('checkbox', { name: '支持对话' })).toBeEnabled()
    expect(screen.getByRole('checkbox', { name: '支持对话' })).toBeChecked()
    expect(screen.getByRole('checkbox', { name: /文件上传/ })).toBeDisabled()
    expect(screen.getByRole('checkbox', { name: /文件上传/ })).not.toBeChecked()
    expect(screen.getByRole('checkbox', { name: /定时任务/ })).toBeDisabled()
    expect(screen.getByRole('checkbox', { name: /定时任务/ })).not.toBeChecked()
    expect(screen.getByText(/文件上传与定时任务尚未接通/)).toBeInTheDocument()

    await user.click(screen.getByRole('combobox', { name: '工作模式' }))
    expect(screen.getByRole('option', { name: '自主执行' })).not.toHaveAttribute(
      'aria-disabled',
      'true',
    )
    expect(screen.getByRole('option', { name: /固定流程.*尚未开放/ })).toHaveAttribute(
      'aria-disabled',
      'true',
    )
    expect(screen.getByRole('option', { name: /混合协作.*尚未开放/ })).toHaveAttribute(
      'aria-disabled',
      'true',
    )
  })

  it('hard-codes the create payload to the configurations the runtime supports', async () => {
    const user = userEvent.setup()
    renderEditor()

    await user.type(screen.getByRole('textbox', { name: '员工名称' }), '研究专员')
    await user.type(screen.getByRole('textbox', { name: '岗位说明' }), '负责研究任务')
    await user.type(screen.getByRole('textbox', { name: '系统指令' }), '完成研究任务')
    await user.click(screen.getByRole('checkbox', { name: '支持对话' }))
    await user.click(screen.getByRole('button', { name: '保存草稿' }))

    await waitFor(() => expect(createMutateAsync).toHaveBeenCalledTimes(1))
    expect(createMutateAsync).toHaveBeenCalledWith(expect.objectContaining({
      work_mode: 'autonomous',
      capabilities: {
        conversation: false,
        scheduled_tasks: false,
        file_upload: false,
      },
    }))
  })

  it('requires an explicit repair before a legacy employee can be saved', async () => {
    const user = userEvent.setup()
    const legacyEmployee: Employee = {
      ...employee,
      definition: {
        ...employee.definition,
        work_mode: 'workflow',
        capabilities: {
          conversation: true,
          scheduled_tasks: true,
          file_upload: true,
        },
      },
    }
    vi.mocked(useEmployee).mockReturnValue({ data: legacyEmployee, isPending: false } as never)
    renderEditor('/employees/employee-1/edit')

    expect(await screen.findByText(/历史配置使用了尚未开放的工作模式/)).toBeInTheDocument()
    expect(screen.getByText(/历史配置声明了尚未接通的能力/)).toBeInTheDocument()
    expect(screen.getByRole('checkbox', { name: /文件上传/ })).toBeDisabled()
    expect(screen.getByRole('checkbox', { name: /文件上传/ })).not.toBeChecked()
    expect(screen.getByRole('checkbox', { name: /定时任务/ })).toBeDisabled()
    expect(screen.getByRole('checkbox', { name: /定时任务/ })).not.toBeChecked()
    expect(screen.getByRole('button', { name: '保存草稿' })).toBeDisabled()

    await user.click(screen.getByRole('button', { name: '切换为自主执行' }))
    expect(screen.getByRole('button', { name: '保存草稿' })).toBeEnabled()
    await user.click(screen.getByRole('button', { name: '保存草稿' }))

    await waitFor(() => expect(updateMutateAsync).toHaveBeenCalledTimes(1))
    expect(updateMutateAsync).toHaveBeenCalledWith(expect.objectContaining({
      work_mode: 'autonomous',
      capabilities: {
        conversation: true,
        scheduled_tasks: false,
        file_upload: false,
      },
    }))
  })

  it('maps a backend unavailable-configuration response to an actionable message', () => {
    vi.mocked(useCreateEmployee).mockReturnValue(mutation({
      isError: true,
      error: {
        isAxiosError: true,
        response: {
          data: { detail: { code: 'employee_configuration_unavailable' } },
        },
      },
    }) as never)
    renderEditor()

    expect(screen.getByText(/切换为自主执行并关闭未接通能力后重试/)).toBeInTheDocument()
  })
})
