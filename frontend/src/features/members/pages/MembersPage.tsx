import {
  Alert,
  Button,
  Card,
  Form,
  Input,
  Popconfirm,
  Select,
  Space,
  Spin,
  Table,
  Tag,
  Typography,
  message,
} from 'antd'
import { useState } from 'react'

import { getApiErrorMessage } from '../../auth/api/errors'
import type { Invitation, Member, TenantRole } from '../api/members'
import {
  useChangeMemberRole,
  useCreateInvitation,
  useInvitations,
  useMembers,
  useRemoveMember,
  useRevokeInvitation,
  useTransferOwner,
  useUpdateTenantSettings,
} from '../api/queries'

const roleColor: Record<TenantRole, string> = {
  owner: 'gold',
  admin: 'blue',
  member: 'default',
}

const roleLabel: Record<TenantRole, string> = {
  owner: 'Owner',
  admin: 'Admin',
  member: 'Member',
}

export function MembersPage({ currentUserId, tenantName }: {
  currentUserId: string
  tenantName: string
}) {
  const members = useMembers()
  const invitations = useInvitations()
  const changeRole = useChangeMemberRole()
  const removeMember = useRemoveMember()
  const transferOwner = useTransferOwner()
  const updateSettings = useUpdateTenantSettings()
  const createInvitation = useCreateInvitation()
  const revokeInvitation = useRevokeInvitation()

  const [name, setName] = useState(tenantName)
  const [inviteEmail, setInviteEmail] = useState('')
  const [inviteRole, setInviteRole] = useState<'admin' | 'member'>('member')
  const [lastToken, setLastToken] = useState<string>()

  const saveSettings = async () => {
    try {
      const updated = await updateSettings.mutateAsync(name)
      message.success(`企业名称已更新为「${updated.name}」`)
    } catch (error) {
      message.error(getApiErrorMessage(error, '更新企业设置失败'))
    }
  }

  const onChangeRole = async (userId: string, role: TenantRole) => {
    try {
      await changeRole.mutateAsync({ userId, role })
      message.success('成员角色已更新')
    } catch (error) {
      message.error(getApiErrorMessage(error, '更新角色失败'))
    }
  }

  const onRemove = async (userId: string) => {
    try {
      await removeMember.mutateAsync(userId)
      message.success('成员已移除')
    } catch (error) {
      message.error(getApiErrorMessage(error, '移除成员失败'))
    }
  }

  const onTransfer = async (userId: string) => {
    try {
      await transferOwner.mutateAsync(userId)
      message.success('企业所有权已转移')
    } catch (error) {
      message.error(getApiErrorMessage(error, '转移所有权失败'))
    }
  }

  const onInvite = async () => {
    try {
      const created = await createInvitation.mutateAsync({ email: inviteEmail, role: inviteRole })
      setLastToken(created.token)
      setInviteEmail('')
      message.success('邀请已创建')
    } catch (error) {
      message.error(getApiErrorMessage(error, '创建邀请失败'))
    }
  }

  const onRevoke = async (invitationId: string) => {
    try {
      await revokeInvitation.mutateAsync(invitationId)
      message.success('邀请已撤销')
    } catch (error) {
      message.error(getApiErrorMessage(error, '撤销邀请失败'))
    }
  }

  const memberColumns = [
    {
      title: '成员',
      key: 'member',
      render: (_: unknown, record: Member) => (
        <Space orientation="vertical" size={0}>
          <Typography.Text>{record.display_name ?? record.email}</Typography.Text>
          <Typography.Text type="secondary">{record.email}</Typography.Text>
        </Space>
      ),
    },
    {
      title: '角色',
      key: 'role',
      render: (_: unknown, record: Member) => (
        <Select
          aria-label={`${record.email} 角色`}
          value={record.role}
          style={{ width: 130 }}
          showSearch
          optionFilterProp="label"
          // 渲染到 body，避免 AntD Table 把下拉塞进可被裁剪/定位到视口外的表格容器。
          getPopupContainer={() => document.body}
          onChange={(role) => void onChangeRole(record.user_id, role)}
          options={[
            { value: 'owner', label: roleLabel.owner },
            { value: 'admin', label: roleLabel.admin },
            { value: 'member', label: roleLabel.member },
          ]}
        />
      ),
    },
    {
      title: '操作',
      key: 'actions',
      render: (_: unknown, record: Member) => (
        <Space>
          {record.user_id !== currentUserId && record.role !== 'owner' && (
            <Popconfirm
              title={`确认将 ${record.email} 设为新 Owner？你将降级为 Admin。`}
              onConfirm={() => void onTransfer(record.user_id)}
            >
              <Button size="small">转为 Owner</Button>
            </Popconfirm>
          )}
          {record.user_id !== currentUserId && (
            <Popconfirm
              title={`确认移除成员 ${record.email}？`}
              onConfirm={() => void onRemove(record.user_id)}
            >
              <Button size="small" danger>移除</Button>
            </Popconfirm>
          )}
        </Space>
      ),
    },
  ]

  const invitationColumns = [
    { title: '邮箱', dataIndex: 'email', key: 'email' },
    {
      title: '角色',
      dataIndex: 'role',
      key: 'role',
      render: (role: TenantRole) => <Tag color={roleColor[role]}>{roleLabel[role]}</Tag>,
    },
    {
      title: '状态',
      dataIndex: 'status',
      key: 'status',
      render: (status: string) => <Tag>{status}</Tag>,
    },
    {
      title: '操作',
      key: 'actions',
      render: (_: unknown, record: Invitation) => (
        <Popconfirm title="确认撤销该邀请？" onConfirm={() => void onRevoke(record.id)}>
          <Button size="small" danger>撤销</Button>
        </Popconfirm>
      ),
    },
  ]

  return (
    <Space orientation="vertical" size="large" style={{ width: '100%' }}>
      <Typography.Title level={2}>企业成员管理</Typography.Title>

      <Card title="企业设置">
        <Form layout="inline" onFinish={() => void saveSettings()}>
          <Form.Item label="企业名称">
            <Input
              aria-label="企业名称"
              value={name}
              onChange={(event) => setName(event.target.value)}
            />
          </Form.Item>
          <Button type="primary" htmlType="submit" loading={updateSettings.isPending}>
            保存
          </Button>
        </Form>
      </Card>

      <Card title="成员">
        {members.isPending && <Spin />}
        {members.isError && (
          <Alert type="error" showIcon title={getApiErrorMessage(members.error, '加载成员失败')} />
        )}
        {members.data && (
          <Table
            rowKey="user_id"
            dataSource={members.data}
            columns={memberColumns}
            pagination={false}
          />
        )}
      </Card>

      <Card title="邀请成员">
        <Space orientation="vertical" size="middle" style={{ width: '100%' }}>
          <Form layout="inline" onFinish={() => void onInvite()}>
            <Form.Item label="邮箱">
              <Input
                aria-label="邀请邮箱"
                type="email"
                value={inviteEmail}
                onChange={(event) => setInviteEmail(event.target.value)}
              />
            </Form.Item>
            <Form.Item label="角色">
              <Select
                aria-label="邀请角色"
                value={inviteRole}
                style={{ width: 130 }}
                showSearch
                optionFilterProp="label"
                getPopupContainer={() => document.body}
                onChange={(role) => setInviteRole(role)}
                options={[
                  { value: 'member', label: roleLabel.member },
                  { value: 'admin', label: roleLabel.admin },
                ]}
              />
            </Form.Item>
            <Button type="primary" htmlType="submit" loading={createInvitation.isPending}>
              发送邀请
            </Button>
          </Form>
          {lastToken && (
            <Alert
              type="success"
              showIcon
              title="邀请已创建"
              description={(
                <Space orientation="vertical" size={4}>
                  <Typography.Text>
                    Demo 阶段不真发信，请把以下邀请令牌交给被邀请者：
                  </Typography.Text>
                  <Typography.Text code copyable data-testid="invitation-token">
                    {lastToken}
                  </Typography.Text>
                </Space>
              )}
            />
          )}
          {invitations.data && invitations.data.length > 0 && (
            <Table
              rowKey="id"
              dataSource={invitations.data}
              columns={invitationColumns}
              pagination={false}
            />
          )}
        </Space>
      </Card>
    </Space>
  )
}
