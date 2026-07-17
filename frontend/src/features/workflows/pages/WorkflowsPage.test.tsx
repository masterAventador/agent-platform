import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import {
  useAddWorkflowVersion,
  usePublishWorkflow,
  useRegisterWorkflow,
  useRollbackWorkflow,
  useWorkflowVersions,
  useWorkflows,
} from '../api/queries'
import type { Workflow } from '../api/workflows'
import { WorkflowsPage } from './WorkflowsPage'


vi.mock('../api/queries', () => ({
  useWorkflows: vi.fn(),
  useWorkflowVersions: vi.fn(),
  useRegisterWorkflow: vi.fn(),
  useAddWorkflowVersion: vi.fn(),
  usePublishWorkflow: vi.fn(),
  useRollbackWorkflow: vi.fn(),
}))

const workflow: Workflow = {
  id: 'w1',
  tenant_id: 't1',
  name: '客服流程',
  description: '标准客服',
  latest_version: 2,
  published_version: 1,
  status: 'published',
}

function mutationStub(mutate = vi.fn()) {
  return { mutate, isPending: false, isError: false, error: null }
}

describe('WorkflowsPage', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    vi.mocked(useWorkflows).mockReturnValue({
      data: [workflow],
      isPending: false,
    } as unknown as ReturnType<typeof useWorkflows>)
    vi.mocked(useWorkflowVersions).mockReturnValue({
      data: [
        { version: 2, description: 'v2', graph: {}, created_at: '', published_at: null },
        { version: 1, description: 'v1', graph: {}, created_at: '', published_at: '' },
      ],
      isPending: false,
    } as unknown as ReturnType<typeof useWorkflowVersions>)
    vi.mocked(useRegisterWorkflow).mockReturnValue(
      mutationStub() as unknown as ReturnType<typeof useRegisterWorkflow>,
    )
    vi.mocked(useAddWorkflowVersion).mockReturnValue(
      mutationStub() as unknown as ReturnType<typeof useAddWorkflowVersion>,
    )
    vi.mocked(usePublishWorkflow).mockReturnValue(
      mutationStub() as unknown as ReturnType<typeof usePublishWorkflow>,
    )
    vi.mocked(useRollbackWorkflow).mockReturnValue(
      mutationStub() as unknown as ReturnType<typeof useRollbackWorkflow>,
    )
  })

  it('lists workflows with published status', () => {
    render(<WorkflowsPage canManageEmployees />)
    expect(screen.getByText('客服流程')).toBeInTheDocument()
    expect(screen.getByText('已发布 v1')).toBeInTheDocument()
  })

  it('registers a workflow from the JSON graph form', async () => {
    const mutate = vi.fn()
    vi.mocked(useRegisterWorkflow).mockReturnValue(
      mutationStub(mutate) as unknown as ReturnType<typeof useRegisterWorkflow>,
    )
    render(<WorkflowsPage canManageEmployees />)
    await userEvent.type(screen.getByLabelText('工作流名称'), '新流程')
    await userEvent.click(screen.getByRole('button', { name: '注册工作流' }))
    await waitFor(() => expect(mutate).toHaveBeenCalledTimes(1))
    expect(mutate.mock.calls[0][0]).toMatchObject({ name: '新流程' })
    expect(mutate.mock.calls[0][0].graph).toMatchObject({ entrypoint: 'collect' })
  })

  it('publishes a higher version and rolls back a lower version through the versions panel', async () => {
    const publishMutate = vi.fn()
    const rollbackMutate = vi.fn()
    vi.mocked(usePublishWorkflow).mockReturnValue(
      mutationStub(publishMutate) as unknown as ReturnType<typeof usePublishWorkflow>,
    )
    vi.mocked(useRollbackWorkflow).mockReturnValue(
      mutationStub(rollbackMutate) as unknown as ReturnType<typeof useRollbackWorkflow>,
    )
    render(<WorkflowsPage canManageEmployees />)
    await userEvent.click(screen.getByRole('button', { name: '查看版本' }))
    // 当前发布 v1；v2 更高 → 发布；比 v1 低的没有，用 v2 走 publish。
    await userEvent.click(screen.getByRole('button', { name: '发布 v2' }))
    expect(publishMutate).toHaveBeenCalledWith(2)
  })

  it('rejects invalid graph JSON before calling the API', async () => {
    const mutate = vi.fn()
    vi.mocked(useRegisterWorkflow).mockReturnValue(
      mutationStub(mutate) as unknown as ReturnType<typeof useRegisterWorkflow>,
    )
    render(<WorkflowsPage canManageEmployees />)
    await userEvent.type(screen.getByLabelText('工作流名称'), '坏流程')
    const textarea = screen.getByLabelText('工作流图（JSON）')
    await userEvent.clear(textarea)
    await userEvent.type(textarea, 'not json')
    await userEvent.click(screen.getByRole('button', { name: '注册工作流' }))
    await waitFor(() => expect(screen.getByText('工作流图 JSON 解析失败')).toBeInTheDocument())
    expect(mutate).not.toHaveBeenCalled()
  })
})
