import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter, Route, Routes } from 'react-router-dom'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import type { Employee } from '../api/employees'
import { useCreateEmployee, useEmployee, useUpdateEmployee } from '../api/queries'
import { useKnowledgeBases } from '../../knowledge/api/queries'
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

vi.mock('../../knowledge/api/queries', () => ({
  useKnowledgeBases: vi.fn(() => ({ data: [], isPending: false })),
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
    model: { kind: 'gateway_alias', alias: 'general-purpose' },
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
}

const knowledgeBaseOptions = [{
  id: 'knowledge-1',
  tenant_id: 'tenant-1',
  name: '员工制度',
  description: 'HR policy',
  provider: 'ragflow',
}]

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
    vi.mocked(useKnowledgeBases).mockReturnValue({ data: [], isPending: false } as never)
    createMutateAsync.mockResolvedValue(employee)
    updateMutateAsync.mockResolvedValue(employee)
  })

  it('shows unavailable modes, enables file uploads and keeps scheduling disabled', async () => {
    const user = userEvent.setup()
    renderEditor()

    expect(screen.getByRole('checkbox', { name: '支持对话' })).toBeEnabled()
    expect(screen.getByRole('checkbox', { name: '支持对话' })).toBeChecked()
    expect(screen.getByRole('checkbox', { name: /文件上传/ })).toBeEnabled()
    expect(screen.getByRole('checkbox', { name: /文件上传/ })).not.toBeChecked()
    expect(screen.getByRole('checkbox', { name: /定时任务/ })).toBeDisabled()
    expect(screen.getByRole('checkbox', { name: /定时任务/ })).not.toBeChecked()
    expect(screen.getByText(/文件上传已接通/)).toBeInTheDocument()

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
    expect(screen.queryByRole('textbox', { name: '模型供应商' })).not.toBeInTheDocument()
    expect(screen.queryByRole('textbox', { name: '模型别名' })).not.toBeInTheDocument()
    expect(screen.getByRole('combobox', { name: '平台模型' })).toBeDisabled()
    expect(screen.getByText('general-purpose')).toBeInTheDocument()
    await user.click(screen.getByRole('checkbox', { name: '支持对话' }))
    await user.click(screen.getByRole('checkbox', { name: /文件上传/ }))
    await user.click(screen.getByRole('button', { name: '保存草稿' }))

    await waitFor(() => expect(createMutateAsync).toHaveBeenCalledTimes(1))
    expect(createMutateAsync).toHaveBeenCalledWith(expect.objectContaining({
      work_mode: 'autonomous',
      model: { kind: 'gateway_alias', alias: 'general-purpose' },
      output_schema: {},
      capabilities: {
        conversation: false,
        scheduled_tasks: false,
        file_upload: true,
      },
    }))
  })

  it('submits configured input and output schemas from the editor form', async () => {
    const user = userEvent.setup()
    renderEditor()

    const inputSchema = {
      type: 'object',
      additionalProperties: false,
      required: ['topic'],
      properties: { topic: { type: 'string', title: '主题', minLength: 2 } },
    }
    const outputSchema = {
      type: 'object',
      properties: { summary: { type: 'string' } },
    }

    await user.type(screen.getByRole('textbox', { name: '员工名称' }), '结构化专员')
    await user.type(screen.getByRole('textbox', { name: '岗位说明' }), '负责结构化任务')
    await user.type(screen.getByRole('textbox', { name: '系统指令' }), '输出结构化结果')
    fireEvent.change(screen.getByRole('textbox', { name: '输入 Schema JSON' }), {
      target: { value: JSON.stringify(inputSchema, null, 2) },
    })
    fireEvent.change(screen.getByRole('textbox', { name: '输出 Schema JSON' }), {
      target: { value: JSON.stringify(outputSchema, null, 2) },
    })
    await user.click(screen.getByRole('button', { name: '保存草稿' }))

    await waitFor(() => expect(createMutateAsync).toHaveBeenCalledTimes(1))
    expect(createMutateAsync).toHaveBeenCalledWith(expect.objectContaining({
      input_schema: inputSchema,
      output_schema: outputSchema,
    }))
  })

  it('submits selected knowledge bases with the employee definition', async () => {
    const user = userEvent.setup()
    vi.mocked(useKnowledgeBases).mockReturnValue({
      data: [{
        id: 'knowledge-1',
        tenant_id: 'tenant-1',
        name: '员工制度',
        description: 'HR policy',
        provider: 'ragflow',
      }],
      isPending: false,
    } as never)
    renderEditor()

    await user.type(screen.getByRole('textbox', { name: '员工名称' }), '制度问答专员')
    await user.type(screen.getByRole('textbox', { name: '岗位说明' }), '回答制度问题')
    await user.type(screen.getByRole('textbox', { name: '系统指令' }), '引用知识库回答')
    await user.click(screen.getByRole('combobox', { name: '知识库' }))
    await user.click(screen.getByRole('option', { name: '员工制度' }))
    await user.click(screen.getByRole('button', { name: '保存草稿' }))

    await waitFor(() => expect(createMutateAsync).toHaveBeenCalledTimes(1))
    expect(createMutateAsync).toHaveBeenCalledWith(expect.objectContaining({
      knowledge_base_ids: ['knowledge-1'],
    }))
  })

  it('submits the default knowledge retrieval config alongside selected knowledge bases', async () => {
    const user = userEvent.setup()
    vi.mocked(useKnowledgeBases).mockReturnValue({
      data: knowledgeBaseOptions,
      isPending: false,
    } as never)
    renderEditor()

    await user.type(screen.getByRole('textbox', { name: '员工名称' }), '制度问答专员')
    await user.type(screen.getByRole('textbox', { name: '岗位说明' }), '回答制度问题')
    await user.type(screen.getByRole('textbox', { name: '系统指令' }), '引用知识库回答')
    await user.click(screen.getByRole('combobox', { name: '知识库' }))
    await user.click(screen.getByRole('option', { name: '员工制度' }))
    await user.click(screen.getByRole('button', { name: '保存草稿' }))

    await waitFor(() => expect(createMutateAsync).toHaveBeenCalledTimes(1))
    expect(createMutateAsync).toHaveBeenCalledWith(expect.objectContaining({
      knowledge_base_ids: ['knowledge-1'],
      knowledge_retrieval: {
        page_size: 5,
        similarity_threshold: 0.2,
        vector_similarity_weight: 0.3,
        top_k: 1024,
        keyword: false,
        rerank_id: null,
        metadata_condition: null,
      },
    }))
  })

  it('submits configured retrieval options, rerank and metadata filters', async () => {
    const user = userEvent.setup()
    vi.mocked(useKnowledgeBases).mockReturnValue({
      data: knowledgeBaseOptions,
      isPending: false,
    } as never)
    renderEditor()

    await user.type(screen.getByRole('textbox', { name: '员工名称' }), '精调检索专员')
    await user.type(screen.getByRole('textbox', { name: '岗位说明' }), '回答制度问题')
    await user.type(screen.getByRole('textbox', { name: '系统指令' }), '引用知识库回答')
    await user.click(screen.getByRole('combobox', { name: '知识库' }))
    await user.click(screen.getByRole('option', { name: '员工制度' }))

    const pageSize = screen.getByRole('spinbutton', { name: '召回条数' })
    await user.clear(pageSize)
    await user.type(pageSize, '8')
    const topK = screen.getByRole('spinbutton', { name: 'Top K' })
    await user.clear(topK)
    await user.type(topK, '256')
    await user.click(screen.getByRole('checkbox', { name: '关键词增强' }))
    await user.type(
      screen.getByRole('textbox', { name: '重排模型 ID' }),
      'BAAI/bge-reranker-v2-m3',
    )
    await user.click(screen.getByRole('button', { name: '添加过滤条件' }))
    await user.type(screen.getByRole('textbox', { name: '字段名' }), 'department')
    await user.type(screen.getByRole('textbox', { name: '比较值' }), 'HR')
    await user.click(screen.getByRole('button', { name: '保存草稿' }))

    await waitFor(() => expect(createMutateAsync).toHaveBeenCalledTimes(1))
    expect(createMutateAsync).toHaveBeenCalledWith(expect.objectContaining({
      knowledge_retrieval: {
        page_size: 8,
        similarity_threshold: 0.2,
        vector_similarity_weight: 0.3,
        top_k: 256,
        keyword: true,
        rerank_id: 'BAAI/bge-reranker-v2-m3',
        metadata_condition: {
          logic: 'and',
          conditions: [
            { name: 'department', comparison_operator: '=', value: 'HR' },
          ],
        },
      },
    }))
  })

  it('loads the persisted retrieval config when editing an employee', async () => {
    const user = userEvent.setup()
    const configured: Employee = {
      ...employee,
      definition: {
        ...employee.definition,
        knowledge_base_ids: ['knowledge-1'],
        knowledge_retrieval: {
          page_size: 9,
          similarity_threshold: 0.5,
          vector_similarity_weight: 0.6,
          top_k: 128,
          keyword: true,
          rerank_id: 'BAAI/bge-reranker-v2-m3',
          metadata_condition: {
            logic: 'or',
            conditions: [
              { name: 'department', comparison_operator: '=', value: 'HR' },
            ],
          },
        },
      },
    }
    vi.mocked(useEmployee).mockReturnValue({ data: configured, isPending: false } as never)
    vi.mocked(useKnowledgeBases).mockReturnValue({
      data: knowledgeBaseOptions,
      isPending: false,
    } as never)
    renderEditor('/employees/employee-1/edit')

    expect(await screen.findByRole('spinbutton', { name: '召回条数' })).toHaveValue('9')
    expect(screen.getByRole('checkbox', { name: '关键词增强' })).toBeChecked()
    expect(screen.getByRole('textbox', { name: '重排模型 ID' })).toHaveValue(
      'BAAI/bge-reranker-v2-m3',
    )
    expect(screen.getByRole('textbox', { name: '字段名' })).toHaveValue('department')

    await user.click(screen.getByRole('button', { name: '保存草稿' }))
    await waitFor(() => expect(updateMutateAsync).toHaveBeenCalledTimes(1))
    expect(updateMutateAsync).toHaveBeenCalledWith(expect.objectContaining({
      knowledge_retrieval: configured.definition.knowledge_retrieval,
    }))
  })

  it('blocks invalid schema JSON before submitting', async () => {
    const user = userEvent.setup()
    renderEditor()

    await user.type(screen.getByRole('textbox', { name: '员工名称' }), '结构化专员')
    await user.type(screen.getByRole('textbox', { name: '岗位说明' }), '负责结构化任务')
    await user.type(screen.getByRole('textbox', { name: '系统指令' }), '输出结构化结果')
    fireEvent.change(screen.getByRole('textbox', { name: '输入 Schema JSON' }), {
      target: { value: '{bad json' },
    })
    await user.click(screen.getByRole('button', { name: '保存草稿' }))

    expect(await screen.findByText('Schema JSON 格式无效')).toBeInTheDocument()
    expect(createMutateAsync).not.toHaveBeenCalled()
  })

  it('does not expose an interaction that can submit another model alias', () => {
    renderEditor()

    expect(screen.getAllByRole('combobox', { name: '平台模型' })).toHaveLength(1)
    expect(screen.getByRole('combobox', { name: '平台模型' })).toBeDisabled()
    expect(screen.queryByRole('textbox', { name: /模型/ })).not.toBeInTheDocument()
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
    expect(screen.getByText(/历史配置使用了尚未接通的定时任务/)).toBeInTheDocument()
    expect(screen.getByRole('checkbox', { name: /文件上传/ })).toBeEnabled()
    expect(screen.getByRole('checkbox', { name: /文件上传/ })).toBeChecked()
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
        file_upload: true,
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

  it('maps a disabled platform model code to an actionable message', () => {
    vi.mocked(useCreateEmployee).mockReturnValue(mutation({
      isError: true,
      error: {
        isAxiosError: true,
        response: {
          status: 422,
          data: { detail: { code: 'employee_model_alias_unavailable' } },
        },
      },
    }) as never)
    renderEditor()

    expect(screen.getByText(/该平台模型未启用.*联系管理员.*可用模型/)).toBeInTheDocument()
  })

  it('maps a backend model-alias 422 response to an actionable message', () => {
    vi.mocked(useCreateEmployee).mockReturnValue(mutation({
      isError: true,
      error: {
        isAxiosError: true,
        response: {
          status: 422,
          data: {
            detail: [{
              type: 'string_pattern_mismatch',
              loc: ['body', 'model', 'alias'],
              msg: 'String should match pattern',
            }],
          },
        },
      },
    }) as never)
    renderEditor()

    expect(screen.getByText(/模型别名无效.*小写字母或数字开头/)).toBeInTheDocument()
  })
})
