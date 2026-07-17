import { Alert, Button, Card, Form, Input, Space, Typography, message } from 'antd'
import { useState } from 'react'
import { Link, useNavigate } from 'react-router-dom'

import { getApiErrorMessage } from '../../auth/api/errors'
import { confirmPasswordReset, requestPasswordReset } from '../api/account'

export function ForgotPasswordPage() {
  const navigate = useNavigate()
  const [email, setEmail] = useState('')
  const [token, setToken] = useState('')
  const [newPassword, setNewPassword] = useState('')
  const [requested, setRequested] = useState(false)
  const [requesting, setRequesting] = useState(false)
  const [confirming, setConfirming] = useState(false)

  const onRequest = async () => {
    setRequesting(true)
    try {
      await requestPasswordReset(email)
      setRequested(true)
    } catch (error) {
      message.error(getApiErrorMessage(error, '提交找回请求失败'))
    } finally {
      setRequesting(false)
    }
  }

  const onConfirm = async () => {
    setConfirming(true)
    try {
      await confirmPasswordReset(token.trim(), newPassword)
      message.success('密码已重置，请用新密码登录')
      navigate('/login', { replace: true })
    } catch (error) {
      message.error(getApiErrorMessage(error, '重置密码失败'))
    } finally {
      setConfirming(false)
    }
  }

  return (
    <main className="auth-page">
      <Card className="auth-card">
        <Space orientation="vertical" size={4} style={{ width: '100%' }}>
          <Typography.Title level={1}>找回密码</Typography.Title>
          <Typography.Text type="secondary">
            输入注册邮箱申请重置；出于安全考虑，无论账号是否存在都会返回相同提示。
          </Typography.Text>
        </Space>

        <Form layout="vertical" onFinish={() => void onRequest()} style={{ marginTop: 16 }}>
          <Form.Item label="邮箱">
            <Input
              aria-label="邮箱"
              type="email"
              value={email}
              onChange={(event) => setEmail(event.target.value)}
              size="large"
            />
          </Form.Item>
          <Button block htmlType="submit" size="large" type="primary" loading={requesting}>
            发送重置请求
          </Button>
        </Form>

        {requested && (
          <>
            <Alert
              style={{ marginTop: 16 }}
              type="info"
              showIcon
              title="若该邮箱已注册，我们已生成重置令牌"
              description="Demo 阶段不真发信，请在下方粘贴重置令牌并设置新密码。"
            />
            <Form layout="vertical" onFinish={() => void onConfirm()} style={{ marginTop: 16 }}>
              <Form.Item label="重置令牌">
                <Input
                  aria-label="重置令牌"
                  value={token}
                  onChange={(event) => setToken(event.target.value)}
                />
              </Form.Item>
              <Form.Item label="新密码（至少 12 位）">
                <Input.Password
                  aria-label="新密码"
                  value={newPassword}
                  onChange={(event) => setNewPassword(event.target.value)}
                />
              </Form.Item>
              <Button block htmlType="submit" size="large" type="primary" loading={confirming}>
                重置密码
              </Button>
            </Form>
          </>
        )}

        <Typography.Paragraph className="auth-switch" type="secondary" style={{ marginTop: 16 }}>
          <Link to="/login">返回登录</Link>
        </Typography.Paragraph>
      </Card>
    </main>
  )
}
