import { Alert, Button, Card, Form, Input, Space, Typography } from 'antd'
import { Link, useNavigate } from 'react-router-dom'

import { getApiErrorMessage } from '../api/errors'
import { useRegister } from '../api/queries'
import './auth.css'

interface RegisterValues {
  email: string
  password: string
  passwordConfirmation: string
}

export function RegisterPage() {
  const register = useRegister()
  const navigate = useNavigate()

  const submit = async ({ email, password }: RegisterValues) => {
    try {
      await register.mutateAsync({ email, password })
      navigate('/login', { replace: true, state: { registered: true, email } })
    } catch {
      // 错误由 Mutation 状态统一渲染。
    }
  }

  return (
    <main className="auth-page">
      <Card className="auth-card">
        <Space className="auth-heading" orientation="vertical" size={4}>
          <Typography.Text className="auth-eyebrow">AI 数字员工平台</Typography.Text>
          <Typography.Title level={1}>创建账号</Typography.Title>
          <Typography.Text type="secondary">内测阶段无需验证邮箱，注册后即可登录</Typography.Text>
        </Space>

        {register.isError && (
          <Alert
            className="auth-alert"
            type="error"
            showIcon
            title={getApiErrorMessage(register.error, '注册失败，请稍后重试')}
          />
        )}

        <Form<RegisterValues> layout="vertical" requiredMark={false} onFinish={submit}>
          <Form.Item label="邮箱" name="email" rules={[{ required: true, type: 'email' }]}>
            <Input autoComplete="email" placeholder="name@company.com" size="large" />
          </Form.Item>
          <Form.Item
            label="密码"
            name="password"
            extra="至少 12 个字符"
            rules={[{ required: true, min: 12 }]}
          >
            <Input.Password autoComplete="new-password" size="large" />
          </Form.Item>
          <Form.Item
            dependencies={['password']}
            label="确认密码"
            name="passwordConfirmation"
            rules={[
              { required: true },
              ({ getFieldValue }) => ({
                validator(_, value) {
                  return !value || getFieldValue('password') === value
                    ? Promise.resolve()
                    : Promise.reject(new Error('两次输入的密码不一致'))
                },
              }),
            ]}
          >
            <Input.Password autoComplete="new-password" size="large" />
          </Form.Item>
          <Button block htmlType="submit" loading={register.isPending} size="large" type="primary">
            创建账号
          </Button>
        </Form>

        <Typography.Paragraph className="auth-switch" type="secondary">
          已有账号？ <Link to="/login">返回登录</Link>
        </Typography.Paragraph>
      </Card>
    </main>
  )
}
