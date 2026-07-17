import { useEffect } from 'react'
import { Alert, Button, Card, Checkbox, Divider, Form, Input, Space, Typography } from 'antd'
import { Link, useLocation, useNavigate } from 'react-router-dom'

import { getPlatformAdapter } from '../../../platform'
import { getApiErrorMessage } from '../api/errors'
import { useLogin } from '../api/queries'
import {
  clearRememberedLogin,
  loadRememberedLogin,
  saveRememberedLogin,
} from '../remembered-login'
import './auth.css'

interface LoginValues {
  email: string
  password: string
  remember: boolean
}

interface LoginLocationState {
  from?: string
  registered?: boolean
  email?: string
}

export function LoginPage() {
  const [form] = Form.useForm<LoginValues>()
  const login = useLogin()
  const navigate = useNavigate()
  const location = useLocation()
  const state = (location.state ?? {}) as LoginLocationState
  const platform = getPlatformAdapter()
  const canRememberLogin = platform.capabilities().rememberedLogin

  useEffect(() => {
    if (!canRememberLogin || state.email) return
    let active = true
    void loadRememberedLogin(platform)
      .then((remembered) => {
        if (active && remembered && !form.isFieldsTouched(['email', 'password'])) {
          form.setFieldsValue({ ...remembered, remember: true })
        }
      })
      .catch(() => console.error('remembered_login_load_failed'))
    return () => {
      active = false
    }
  }, [canRememberLogin, form, platform, state.email])

  const submit = async (values: LoginValues) => {
    const { remember, ...credentials } = values
    try {
      await login.mutateAsync(credentials)
      try {
        if (remember) {
          await saveRememberedLogin(platform, credentials)
        } else {
          await clearRememberedLogin(platform)
        }
      } catch {
        console.error('remembered_login_update_failed')
      }
      navigate(state.from ?? '/', { replace: true })
    } catch {
      // 错误由 Mutation 状态统一渲染。
    }
  }

  return (
    <main className="auth-page">
      <Card className="auth-card">
        <Space className="auth-heading" orientation="vertical" size={4}>
          <Typography.Text className="auth-eyebrow">AI 数字员工平台</Typography.Text>
          <Typography.Title level={1}>登录</Typography.Title>
          <Typography.Text type="secondary">使用企业邮箱进入你的数字员工工作台</Typography.Text>
        </Space>

        {state.registered && (
          <Alert className="auth-alert" type="success" showIcon title="注册成功，请登录" />
        )}
        {login.isError && (
          <Alert
            className="auth-alert"
            type="error"
            showIcon
            title={getApiErrorMessage(login.error, '登录失败，请稍后重试')}
          />
        )}

        <Form<LoginValues>
          form={form}
          layout="vertical"
          initialValues={{ email: state.email, remember: canRememberLogin }}
          requiredMark={false}
          onFinish={submit}
        >
          <Form.Item label="邮箱" name="email" rules={[{ required: true, type: 'email' }]}>
            <Input autoComplete="email" placeholder="name@company.com" size="large" />
          </Form.Item>
          <Form.Item label="密码" name="password" rules={[{ required: true }]}>
            <Input.Password autoComplete="current-password" size="large" />
          </Form.Item>
          {canRememberLogin && (
            <Form.Item className="auth-remember" name="remember" valuePropName="checked">
              <Checkbox>记住账号和密码（仅保存在此设备的 App 数据中）</Checkbox>
            </Form.Item>
          )}
          <Button block htmlType="submit" loading={login.isPending} size="large" type="primary">
            登录
          </Button>
        </Form>

        <Divider>或</Divider>
        <Button block disabled size="large">企业 SSO（即将支持）</Button>
        <Typography.Paragraph className="auth-switch" type="secondary">
          还没有账号？ <Link to="/register">注册账号</Link>
        </Typography.Paragraph>
        <Typography.Paragraph className="auth-switch" type="secondary">
          <Link to="/forgot-password">忘记密码？</Link>
        </Typography.Paragraph>
      </Card>
    </main>
  )
}
