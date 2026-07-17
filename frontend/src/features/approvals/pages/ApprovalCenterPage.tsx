import {
  Alert,
  Button,
  Descriptions,
  Flex,
  Form,
  Input,
  Modal,
  Space,
  Spin,
  Table,
  Tabs,
  Tag,
  Typography,
} from 'antd'
import { useState } from 'react'
import { Link } from 'react-router-dom'

import type { Approval, ApprovalStatus, ApprovalView } from '../api/approvals'
import {
  useApprovals,
  useApproveApproval,
  useRejectApproval,
  useTransferApproval,
  useWithdrawApproval,
} from '../api/queries'


const statusLabels: Record<ApprovalStatus, { color: string; text: string }> = {
  pending: { color: 'warning', text: '待审批' },
  approved: { color: 'success', text: '已批准' },
  rejected: { color: 'error', text: '已拒绝' },
  expired: { color: 'default', text: '已过期' },
  withdrawn: { color: 'default', text: '已撤回' },
  transferred: { color: 'processing', text: '已转交' },
}

const riskLabels: Record<string, { color: string; text: string }> = {
  read: { color: 'default', text: '只读' },
  write: { color: 'blue', text: '写入' },
  external: { color: 'orange', text: '外部操作' },
  destructive: { color: 'red', text: '破坏性' },
  unknown: { color: 'default', text: '未知' },
}

type ApprovalAction = 'approve' | 'reject' | 'transfer' | 'withdraw'

const actionTitles: Record<ApprovalAction, { title: string; confirm: string }> = {
  approve: { title: '批准审批', confirm: '确认批准' },
  reject: { title: '拒绝审批', confirm: '确认拒绝' },
  transfer: { title: '转交审批', confirm: '确认转交' },
  withdraw: { title: '撤回审批请求', confirm: '确认撤回' },
}

interface ActionFormValues {
  reason?: string
  assigneeEmail?: string
}

export function ApprovalCenterPage({ currentUserId }: { currentUserId: string }) {
  const [view, setView] = useState<ApprovalView>('pending')
  const [pendingAction, setPendingAction] = useState<
    { action: ApprovalAction; approval: Approval } | undefined
  >()
  const [actionError, setActionError] = useState<string>()
  const [form] = Form.useForm<ActionFormValues>()
  const approvals = useApprovals(view)
  const approve = useApproveApproval()
  const reject = useRejectApproval()
  const transfer = useTransferApproval()
  const withdraw = useWithdrawApproval()

  const isSubmitting =
    approve.isPending || reject.isPending || transfer.isPending || withdraw.isPending

  const openAction = (action: ApprovalAction, approval: Approval) => {
    setActionError(undefined)
    form.resetFields()
    setPendingAction({ action, approval })
  }

  const closeAction = () => {
    setPendingAction(undefined)
    setActionError(undefined)
    form.resetFields()
  }

  const submitAction = async () => {
    if (pendingAction === undefined) return
    let values: ActionFormValues
    try {
      values = await form.validateFields()
    } catch {
      return // 校验失败：错误信息由表单内联展示
    }
    const options = {
      onSuccess: closeAction,
      onError: (error: unknown) => setActionError(describeActionError(error)),
    }
    const approvalId = pendingAction.approval.id
    if (pendingAction.action === 'approve') {
      approve.mutate(
        { approvalId, payload: values.reason ? { reason: values.reason } : {} },
        options,
      )
    } else if (pendingAction.action === 'reject') {
      reject.mutate({ approvalId, payload: { reason: values.reason ?? '' } }, options)
    } else if (pendingAction.action === 'transfer') {
      transfer.mutate(
        {
          approvalId,
          payload: {
            assignee_email: values.assigneeEmail ?? '',
            ...(values.reason ? { reason: values.reason } : {}),
          },
        },
        options,
      )
    } else {
      withdraw.mutate(
        { approvalId, payload: values.reason ? { reason: values.reason } : {} },
        options,
      )
    }
  }

  return (
    <section>
      <Typography.Title level={2}>审批中心</Typography.Title>
      <Typography.Paragraph type="secondary">
        集中处理数字员工触发的高风险操作审批：批准、拒绝、转交与撤回。
      </Typography.Paragraph>
      <Tabs
        activeKey={view}
        onChange={(key) => setView(key as ApprovalView)}
        items={[
          { key: 'pending', label: '待办' },
          { key: 'history', label: '历史' },
        ]}
      />
      {approvals.isPending ? (
        <Flex justify="center" aria-label="正在加载审批数据">
          <Spin />
        </Flex>
      ) : approvals.isError || approvals.data === undefined ? (
        <Alert
          type="error"
          showIcon
          title="审批数据加载失败"
          action={<Button onClick={() => void approvals.refetch()}>重新加载</Button>}
        />
      ) : (
        <Table<Approval>
          rowKey="id"
          dataSource={approvals.data.items}
          pagination={false}
          locale={{ emptyText: view === 'pending' ? '暂无待办审批' : '暂无历史审批' }}
          expandable={{
            expandedRowRender: (approval) => <ApprovalDetail approval={approval} />,
          }}
          columns={[
            {
              title: '工具',
              key: 'tool',
              render: (_, approval) => (
                <Typography.Text strong>
                  {typeof approval.context.tool_name === 'string'
                    ? approval.context.tool_name
                    : approval.approval_type}
                </Typography.Text>
              ),
            },
            {
              title: '风险',
              key: 'risk',
              render: (_, approval) => {
                const risk = riskLabels[approval.risk_level] ?? riskLabels.unknown
                return <Tag color={risk.color}>{risk.text}</Tag>
              },
            },
            {
              title: '状态',
              key: 'status',
              render: (_, approval) => (
                <Tag color={statusLabels[approval.status].color}>
                  {statusLabels[approval.status].text}
                </Tag>
              ),
            },
            {
              title: '创建时间',
              key: 'created_at',
              render: (_, approval) => new Date(approval.created_at).toLocaleString(),
            },
            {
              title: '过期时间',
              key: 'expires_at',
              render: (_, approval) =>
                approval.expires_at === null
                  ? '—'
                  : new Date(approval.expires_at).toLocaleString(),
            },
            ...(view === 'history'
              ? [
                  {
                    title: '理由',
                    key: 'reason',
                    render: (_: unknown, approval: Approval) => approval.reason ?? '—',
                  },
                ]
              : [
                  {
                    title: '操作',
                    key: 'actions',
                    render: (_: unknown, approval: Approval) => (
                      <Space wrap>
                        <Button
                          size="small"
                          type="primary"
                          disabled={approval.status !== 'pending'}
                          onClick={() => openAction('approve', approval)}
                        >
                          批准
                        </Button>
                        <Button
                          size="small"
                          danger
                          disabled={approval.status !== 'pending'}
                          onClick={() => openAction('reject', approval)}
                        >
                          拒绝
                        </Button>
                        <Button
                          size="small"
                          disabled={approval.status !== 'pending'}
                          onClick={() => openAction('transfer', approval)}
                        >
                          转交
                        </Button>
                        {approval.requested_by === currentUserId && (
                          <Button
                            size="small"
                            disabled={approval.status !== 'pending'}
                            onClick={() => openAction('withdraw', approval)}
                          >
                            撤回
                          </Button>
                        )}
                      </Space>
                    ),
                  },
                ]),
          ]}
        />
      )}
      <Modal
        open={pendingAction !== undefined}
        title={pendingAction === undefined ? '' : actionTitles[pendingAction.action].title}
        onCancel={closeAction}
        footer={null}
        destroyOnHidden
      >
        {actionError && <Alert type="error" showIcon title={actionError} />}
        <Form form={form} layout="vertical" preserve={false}>
          {pendingAction?.action === 'transfer' && (
            <Form.Item
              name="assigneeEmail"
              label="被转交人邮箱"
              rules={[
                { required: true, message: '请输入被转交人邮箱' },
                { type: 'email', message: '请输入有效邮箱' },
              ]}
            >
              <Input placeholder="admin@example.com" />
            </Form.Item>
          )}
          <Form.Item
            name="reason"
            label="理由"
            rules={
              pendingAction?.action === 'reject'
                ? [{ required: true, message: '拒绝审批必须填写理由' }]
                : []
            }
          >
            <Input.TextArea rows={3} placeholder="填写审批理由" />
          </Form.Item>
          <Flex justify="flex-end" gap={8}>
            <Button onClick={closeAction}>取消</Button>
            <Button
              type="primary"
              loading={isSubmitting}
              onClick={() => void submitAction()}
            >
              {pendingAction === undefined ? '确认' : actionTitles[pendingAction.action].confirm}
            </Button>
          </Flex>
        </Form>
      </Modal>
    </section>
  )
}

function ApprovalDetail({ approval }: { approval: Approval }) {
  return (
    <Descriptions
      size="small"
      column={1}
      items={[
        {
          key: 'context',
          label: '业务上下文',
          children: (
            <pre className="approval-context">
              {JSON.stringify(approval.context, null, 2)}
            </pre>
          ),
        },
        {
          key: 'run',
          label: '关联任务',
          children:
            approval.run_id === null ? (
              '—'
            ) : (
              <Link to={`/runs/${approval.run_id}`}>{approval.run_id}</Link>
            ),
        },
        {
          key: 'chain',
          label: '转交链',
          children:
            approval.transferred_from_id === null && approval.transferred_to_id === null
              ? '—'
              : [
                  approval.transferred_from_id === null
                    ? null
                    : `来自 ${approval.transferred_from_id}`,
                  approval.transferred_to_id === null
                    ? null
                    : `转至 ${approval.transferred_to_id}`,
                ]
                  .filter(Boolean)
                  .join('；'),
        },
        {
          key: 'decision',
          label: '决策',
          children:
            approval.decided_at === null
              ? '—'
              : `${new Date(approval.decided_at).toLocaleString()}${
                  approval.reason ? `：${approval.reason}` : ''
                }`,
        },
      ]}
    />
  )
}

function describeActionError(error: unknown): string {
  const detail = (error as {
    response?: { data?: { detail?: { code?: string; message?: string } } }
  })?.response?.data?.detail
  if (detail?.message) return detail.message
  return '操作失败，请刷新后重试'
}
