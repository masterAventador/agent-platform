import { Button, Flex, Layout, Space, Typography } from 'antd'
import { Link, Route, Routes, useNavigate } from 'react-router-dom'

import { useCurrentUser, useLogout } from '../features/auth/api/queries'
import { ProtectedRoute } from '../features/auth/components/ProtectedRoute'
import { LoginPage } from '../features/auth/pages/LoginPage'
import { RegisterPage } from '../features/auth/pages/RegisterPage'
import { BackendStatus } from '../features/system/components/BackendStatus'
import './app.css'

const { Content, Sider } = Layout

export function App() {
  return (
    <Routes>
      <Route path="/login" element={<LoginPage />} />
      <Route path="/register" element={<RegisterPage />} />
      <Route
        path="*"
        element={
          <ProtectedRoute>
            <PlatformShell />
          </ProtectedRoute>
        }
      />
    </Routes>
  )
}

function PlatformShell() {
  const currentUser = useCurrentUser()
  const logout = useLogout()
  const navigate = useNavigate()

  const signOut = async () => {
    await logout.mutateAsync()
    navigate('/login', { replace: true })
  }

  return (
    <Layout className="app-shell">
      <Sider className="app-sidebar" width={240}>
        <Typography.Title className="app-title" level={1}>
          AI 数字员工平台
        </Typography.Title>
        <nav aria-label="主导航">
          <Space className="app-navigation" orientation="vertical" size="middle">
            <Link to="/">工作台</Link>
            <Link to="/employees">数字员工</Link>
            <Link to="/runs">任务中心</Link>
          </Space>
        </nav>
      </Sider>
      <Content className="app-content">
        <Flex align="center" justify="space-between">
          <div>
            <Typography.Title level={2}>工作台</Typography.Title>
            <Typography.Text type="secondary">{currentUser.data?.email}</Typography.Text>
          </div>
          <Button loading={logout.isPending} onClick={signOut}>退出登录</Button>
        </Flex>
        <Typography.Paragraph type="secondary">
          平台基础工程已就绪，后续功能将按前后端纵向切片逐步接入。
        </Typography.Paragraph>
        <BackendStatus />
      </Content>
    </Layout>
  )
}
