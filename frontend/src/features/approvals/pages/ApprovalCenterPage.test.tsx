import { render, screen, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import type { Approval } from '../api/approvals'
import {
  useApprovals,
  useApproveApproval,
  useRejectApproval,
  useTransferApproval,
  useWithdrawApproval,
} from '../api/queries'
import { ApprovalCenterPage } from './ApprovalCenterPage'


vi.mock('../api/queries', () => ({
  useApprovals: vi.fn(),
  useApproveApproval: vi.fn(),
  useRejectApproval: vi.fn(),
  useTransferApproval: vi.fn(),
  useWithdrawApproval: vi.fn(),
}))

const approveMutate = vi.fn()
const rejectMutate = vi.fn()
const transferMutate = vi.fn()
const withdrawMutate = vi.fn()

const pendingApproval: Approval = {
  id: '20000000-0000-4000-8000-000000000020',
  tenant_id: '10000000-0000-4000-8000-000000000010',
  source: 'tool_risk',
  approval_type: 'tool.invocation',
  risk_level: 'external',
  status: 'pending',
  requested_by: '30000000-0000-4000-8000-000000000030',
  required_role: 'admin',
  context: { tool_name: 'send_email', arguments: { to: 'a@b.c' } },
  run_id: '40000000-0000-4000-8000-000000000040',
  invocation_id: '50000000-0000-4000-8000-000000000050',
  employee_id: null,
  assignee_id: null,
  decided_by: null,
  reason: null,
  decided_at: null,
  created_at: '2026-07-17T08:00:00Z',
  expires_at: '2026-07-18T08:00:00Z',
  transferred_from_id: null,
  transferred_to_id: null,
  revision: 1,
}

function mockMutation(mutate: ReturnType<typeof vi.fn>) {
  return { mutate, mutateAsync: mutate, isPending: false } as unknown
}

describe('ApprovalCenterPage', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    vi.mocked(useApprovals).mockReturnValue({
      data: { items: [pendingApproval], total: 1, limit: 50, offset: 0 },
      isPending: false,
      isError: false,
      refetch: vi.fn(),
    } as unknown as ReturnType<typeof useApprovals>)
    vi.mocked(useApproveApproval).mockReturnValue(
      mockMutation(approveMutate) as ReturnType<typeof useApproveApproval>,
    )
    vi.mocked(useRejectApproval).mockReturnValue(
      mockMutation(rejectMutate) as ReturnType<typeof useRejectApproval>,
    )
    vi.mocked(useTransferApproval).mockReturnValue(
      mockMutation(transferMutate) as ReturnType<typeof useTransferApproval>,
    )
    vi.mocked(useWithdrawApproval).mockReturnValue(
      mockMutation(withdrawMutate) as ReturnType<typeof useWithdrawApproval>,
    )
  })

  it('展示待办列表：工具、风险、状态与过期时间', () => {
    render(<ApprovalCenterPage currentUserId={pendingApproval.requested_by} />)

    expect(screen.getByRole('heading', { name: '审批中心' })).toBeInTheDocument()
    expect(screen.getByText('send_email')).toBeInTheDocument()
    expect(screen.getByText('外部操作')).toBeInTheDocument()
    expect(screen.getByText('待审批')).toBeInTheDocument()
  })

  it('批准需确认并附可选理由', async () => {
    const user = userEvent.setup()
    render(<ApprovalCenterPage currentUserId={pendingApproval.requested_by} />)

    await user.click(screen.getByRole('button', { name: /批\s*准/ }))
    const dialog = await screen.findByRole('dialog')
    await user.type(within(dialog).getByLabelText('理由'), '允许执行')
    await user.click(within(dialog).getByRole('button', { name: '确认批准' }))

    expect(approveMutate).toHaveBeenCalledWith(
      expect.objectContaining({
        approvalId: pendingApproval.id,
        payload: { reason: '允许执行' },
      }),
      expect.anything(),
    )
  })

  it('拒绝必须填写理由', async () => {
    const user = userEvent.setup()
    render(<ApprovalCenterPage currentUserId={pendingApproval.requested_by} />)

    await user.click(screen.getByRole('button', { name: /拒\s*绝/ }))
    const dialog = await screen.findByRole('dialog')
    await user.click(within(dialog).getByRole('button', { name: '确认拒绝' }))
    expect(rejectMutate).not.toHaveBeenCalled()

    await user.type(within(dialog).getByLabelText('理由'), '不允许外发')
    await user.click(within(dialog).getByRole('button', { name: '确认拒绝' }))

    expect(rejectMutate).toHaveBeenCalledWith(
      expect.objectContaining({
        approvalId: pendingApproval.id,
        payload: { reason: '不允许外发' },
      }),
      expect.anything(),
    )
  })

  it('转交需要填写被转交人邮箱', async () => {
    const user = userEvent.setup()
    render(<ApprovalCenterPage currentUserId={pendingApproval.requested_by} />)

    await user.click(screen.getByRole('button', { name: /转\s*交/ }))
    const dialog = await screen.findByRole('dialog')
    await user.type(within(dialog).getByLabelText('被转交人邮箱'), 'admin@example.com')
    await user.click(within(dialog).getByRole('button', { name: '确认转交' }))

    expect(transferMutate).toHaveBeenCalledWith(
      expect.objectContaining({
        approvalId: pendingApproval.id,
        payload: expect.objectContaining({ assignee_email: 'admin@example.com' }),
      }),
      expect.anything(),
    )
  })

  it('发起人可以撤回自己的审批请求', async () => {
    const user = userEvent.setup()
    render(<ApprovalCenterPage currentUserId={pendingApproval.requested_by} />)

    await user.click(screen.getByRole('button', { name: /撤\s*回/ }))
    const dialog = await screen.findByRole('dialog')
    await user.click(within(dialog).getByRole('button', { name: '确认撤回' }))

    expect(withdrawMutate).toHaveBeenCalledWith(
      expect.objectContaining({ approvalId: pendingApproval.id }),
      expect.anything(),
    )
  })

  it('非发起人看不到撤回入口', () => {
    render(<ApprovalCenterPage currentUserId="99999999-0000-4000-8000-000000000099" />)

    expect(screen.queryByRole('button', { name: /撤\s*回/ })).not.toBeInTheDocument()
  })

  it('历史标签展示终态记录且无操作按钮', async () => {
    const user = userEvent.setup()
    vi.mocked(useApprovals).mockImplementation(((view: 'pending' | 'history') => ({
      data: {
        items:
          view === 'history'
            ? [{ ...pendingApproval, status: 'rejected', reason: '风险过高' }]
            : [],
        total: view === 'history' ? 1 : 0,
        limit: 50,
        offset: 0,
      },
      isPending: false,
      isError: false,
      refetch: vi.fn(),
    })) as unknown as typeof useApprovals)

    render(<ApprovalCenterPage currentUserId={pendingApproval.requested_by} />)
    await user.click(screen.getByRole('tab', { name: '历史' }))

    expect(await screen.findByText('已拒绝')).toBeInTheDocument()
    expect(screen.getByText('风险过高')).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: /批\s*准/ })).not.toBeInTheDocument()
  })
})
