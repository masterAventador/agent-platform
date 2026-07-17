import {
  Alert,
  Button,
  Card,
  Form,
  Input,
  Popconfirm,
  Space,
  Spin,
  Table,
  Tag,
  Typography,
  message,
} from 'antd'
import { useState } from 'react'

import { getApiErrorMessage } from '../../auth/api/errors'
import { useAcceptInvitation } from '../../members/api/queries'
import type { SessionInfo } from '../api/account'
import {
  useChangePassword,
  useConfirmEmailVerification,
  useProfile,
  useRequestEmailVerification,
  useRevokeOtherSessions,
  useRevokeSession,
  useSessions,
  useUpdateProfile,
} from '../api/queries'

const formatDateTime = (value: string) => new Date(value).toLocaleString('zh-CN', {
  year: 'numeric',
  month: '2-digit',
  day: '2-digit',
  hour: '2-digit',
  minute: '2-digit',
  hour12: false,
})

export function AccountSettingsPage() {
  const profile = useProfile()
  const sessions = useSessions()
  const updateProfile = useUpdateProfile()
  const changePassword = useChangePassword()
  const requestVerification = useRequestEmailVerification()
  const confirmVerification = useConfirmEmailVerification()
  const revokeSession = useRevokeSession()
  const revokeOthers = useRevokeOtherSessions()
  const acceptInvitation = useAcceptInvitation()

  const [displayName, setDisplayName] = useState<string>()
  const [currentPassword, setCurrentPassword] = useState('')
  const [newPassword, setNewPassword] = useState('')
  const [verificationToken, setVerificationToken] = useState('')
  const [invitationToken, setInvitationToken] = useState('')

  const effectiveDisplayName = displayName ?? profile.data?.display_name ?? ''

  const onSaveProfile = async () => {
    try {
      await updateProfile.mutateAsync(effectiveDisplayName.trim() || null)
      message.success('资料已更新')
    } catch (error) {
      message.error(getApiErrorMessage(error, '更新资料失败'))
    }
  }

  const onChangePassword = async () => {
    try {
      await changePassword.mutateAsync({ currentPassword, newPassword })
      setCurrentPassword('')
      setNewPassword('')
      message.success('密码已修改，其它设备的登录已失效')
    } catch (error) {
      message.error(getApiErrorMessage(error, '修改密码失败'))
    }
  }

  const onRequestVerification = async () => {
    try {
      const token = await requestVerification.mutateAsync()
      if (token) setVerificationToken(token)
      message.success('验证令牌已生成')
    } catch (error) {
      message.error(getApiErrorMessage(error, '生成验证令牌失败'))
    }
  }

  const onConfirmVerification = async () => {
    try {
      await confirmVerification.mutateAsync(verificationToken)
      setVerificationToken('')
      message.success('邮箱已验证')
    } catch (error) {
      message.error(getApiErrorMessage(error, '邮箱验证失败'))
    }
  }

  const onRevokeSession = async (sessionId: string) => {
    try {
      await revokeSession.mutateAsync(sessionId)
      message.success('会话已撤销')
    } catch (error) {
      message.error(getApiErrorMessage(error, '撤销会话失败'))
    }
  }

  const onRevokeOthers = async () => {
    try {
      await revokeOthers.mutateAsync()
      message.success('已退出其它设备')
    } catch (error) {
      message.error(getApiErrorMessage(error, '退出其它设备失败'))
    }
  }

  const onAcceptInvitation = async () => {
    try {
      await acceptInvitation.mutateAsync(invitationToken.trim())
      setInvitationToken('')
      message.success('已加入企业，可在左上角切换工作区')
    } catch (error) {
      message.error(getApiErrorMessage(error, '接受邀请失败'))
    }
  }

  const sessionColumns = [
    {
      title: '登录时间',
      dataIndex: 'created_at',
      key: 'created_at',
      render: (value: string) => formatDateTime(value),
    },
    {
      title: '设备',
      dataIndex: 'user_agent',
      key: 'user_agent',
      render: (value: string | null) => value ?? '未知设备',
    },
    {
      title: '状态',
      key: 'status',
      render: (_: unknown, record: SessionInfo) => (
        <Space size={4}>
          {record.current && <Tag color="green">当前设备</Tag>}
          {!record.active && <Tag>已失效</Tag>}
          {record.active && !record.current && <Tag color="blue">活跃</Tag>}
        </Space>
      ),
    },
    {
      title: '操作',
      key: 'actions',
      render: (_: unknown, record: SessionInfo) => (
        !record.current && record.active
          ? (
            <Popconfirm title="确认撤销该会话？" onConfirm={() => void onRevokeSession(record.id)}>
              <Button size="small" danger>撤销</Button>
            </Popconfirm>
          )
          : null
      ),
    },
  ]

  return (
    <Space orientation="vertical" size="large" style={{ width: '100%' }}>
      <Typography.Title level={2}>账号设置</Typography.Title>

      <Card title="个人资料">
        {profile.isPending && <Spin />}
        {profile.data && (
          <Space orientation="vertical" size="middle" style={{ width: '100%' }}>
            <Typography.Text>
              邮箱：{profile.data.email}
              {profile.data.email_verified
                ? <Tag color="green" style={{ marginLeft: 8 }}>已验证</Tag>
                : <Tag color="orange" style={{ marginLeft: 8 }}>未验证</Tag>}
            </Typography.Text>
            <Form layout="inline" onFinish={() => void onSaveProfile()}>
              <Form.Item label="昵称">
                <Input
                  aria-label="昵称"
                  value={effectiveDisplayName}
                  onChange={(event) => setDisplayName(event.target.value)}
                />
              </Form.Item>
              <Button type="primary" htmlType="submit" loading={updateProfile.isPending}>
                保存
              </Button>
            </Form>
            {!profile.data.email_verified && (
              <Space orientation="vertical" size="small" style={{ width: '100%' }}>
                <Button onClick={() => void onRequestVerification()} loading={requestVerification.isPending}>
                  发送邮箱验证
                </Button>
                {verificationToken && (
                  <Space>
                    <Input
                      aria-label="邮箱验证令牌"
                      value={verificationToken}
                      onChange={(event) => setVerificationToken(event.target.value)}
                      style={{ width: 360 }}
                    />
                    <Button
                      type="primary"
                      onClick={() => void onConfirmVerification()}
                      loading={confirmVerification.isPending}
                    >
                      确认验证
                    </Button>
                  </Space>
                )}
              </Space>
            )}
          </Space>
        )}
      </Card>

      <Card title="接受企业邀请">
        <Form layout="inline" onFinish={() => void onAcceptInvitation()}>
          <Form.Item label="邀请令牌">
            <Input
              aria-label="邀请令牌"
              value={invitationToken}
              onChange={(event) => setInvitationToken(event.target.value)}
              style={{ width: 360 }}
            />
          </Form.Item>
          <Button type="primary" htmlType="submit" loading={acceptInvitation.isPending}>
            接受邀请
          </Button>
        </Form>
      </Card>

      <Card title="修改密码">
        <Form layout="vertical" onFinish={() => void onChangePassword()} style={{ maxWidth: 360 }}>
          <Form.Item label="当前密码">
            <Input.Password
              aria-label="当前密码"
              value={currentPassword}
              onChange={(event) => setCurrentPassword(event.target.value)}
            />
          </Form.Item>
          <Form.Item label="新密码（至少 12 位）">
            <Input.Password
              aria-label="新密码"
              value={newPassword}
              onChange={(event) => setNewPassword(event.target.value)}
            />
          </Form.Item>
          <Button type="primary" htmlType="submit" loading={changePassword.isPending}>
            修改密码
          </Button>
        </Form>
      </Card>

      <Card
        title="登录设备"
        extra={(
          <Popconfirm title="确认退出其它所有设备？" onConfirm={() => void onRevokeOthers()}>
            <Button danger loading={revokeOthers.isPending}>退出其它设备</Button>
          </Popconfirm>
        )}
      >
        {sessions.isPending && <Spin />}
        {sessions.isError && (
          <Alert
            type="error"
            showIcon
            title={getApiErrorMessage(sessions.error, '加载会话失败')}
          />
        )}
        {sessions.data && (
          <Table
            rowKey="id"
            dataSource={sessions.data}
            columns={sessionColumns}
            pagination={false}
          />
        )}
      </Card>
    </Space>
  )
}
