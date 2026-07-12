import { Button, Flex, Layout, Space, Typography } from 'antd'
import { lazy, Suspense } from 'react'
import { Link, Route, Routes, useNavigate } from 'react-router-dom'

import { useCurrentUser, useLogout } from '../features/auth/api/queries'
import { ProtectedRoute } from '../features/auth/components/ProtectedRoute'
import { BackendStatus } from '../features/system/components/BackendStatus'
import './app.css'

const { Content, Sider } = Layout
const LoginPage = lazy(() =>
  import('../features/auth/pages/LoginPage').then((module) => ({ default: module.LoginPage })),
)
const RegisterPage = lazy(() =>
  import('../features/auth/pages/RegisterPage').then((module) => ({
    default: module.RegisterPage,
  })),
)
const EmployeesPage = lazy(() =>
  import('../features/employees/pages/EmployeesPage').then((module) => ({
    default: module.EmployeesPage,
  })),
)
const EmployeeEditorPage = lazy(() =>
  import('../features/employees/pages/EmployeeEditorPage').then((module) => ({
    default: module.EmployeeEditorPage,
  })),
)
const EmployeeDetailPage = lazy(() =>
  import('../features/employees/pages/EmployeeDetailPage').then((module) => ({
    default: module.EmployeeDetailPage,
  })),
)
const RunsPage = lazy(() =>
  import('../features/runs/pages/RunsPage').then((module) => ({ default: module.RunsPage })),
)
const RunDetailPage = lazy(() =>
  import('../features/runs/pages/RunDetailPage').then((module) => ({
    default: module.RunDetailPage,
  })),
)
const KnowledgeBasesPage = lazy(() =>
  import('../features/knowledge/pages/KnowledgeBasesPage').then((module) => ({
    default: module.KnowledgeBasesPage,
  })),
)
const KnowledgeBaseDetailPage = lazy(() =>
  import('../features/knowledge/pages/KnowledgeBaseDetailPage').then((module) => ({
    default: module.KnowledgeBaseDetailPage,
  })),
)
const SkillsPage = lazy(() =>
  import('../features/skills/pages/SkillsPage').then((module) => ({
    default: module.SkillsPage,
  })),
)
const SkillDetailPage = lazy(() =>
  import('../features/skills/pages/SkillDetailPage').then((module) => ({
    default: module.SkillDetailPage,
  })),
)

export function App() {
  return (
    <Suspense fallback={<RouteLoading />}>
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
    </Suspense>
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
            <Link to="/knowledge-bases">知识库</Link>
            <Link to="/skills">Skill 中心</Link>
          </Space>
        </nav>
      </Sider>
      <Content className="app-content">
        <Flex className="app-topbar" align="center" justify="flex-end" gap={16}>
          <Typography.Text type="secondary">{currentUser.data?.email}</Typography.Text>
          <Button loading={logout.isPending} onClick={signOut}>
            退出登录
          </Button>
        </Flex>
        <Routes>
          <Route path="/" element={<Dashboard />} />
          <Route path="/employees" element={<EmployeesPage />} />
          <Route path="/employees/new" element={<EmployeeEditorPage />} />
          <Route path="/employees/:employeeId" element={<EmployeeDetailPage />} />
          <Route path="/employees/:employeeId/edit" element={<EmployeeEditorPage />} />
          <Route path="/runs" element={<RunsPage />} />
          <Route path="/runs/:runId" element={<RunDetailPage />} />
          <Route path="/knowledge-bases" element={<KnowledgeBasesPage />} />
          <Route path="/knowledge-bases/:knowledgeBaseId" element={<KnowledgeBaseDetailPage />} />
          <Route path="/skills" element={<SkillsPage />} />
          <Route path="/skills/:skillId" element={<SkillDetailPage />} />
        </Routes>
      </Content>
    </Layout>
  )
}

function Dashboard() {
  return (
    <section>
      <Typography.Title level={2}>工作台</Typography.Title>
      <Typography.Paragraph type="secondary">
        平台基础工程已就绪，后续功能将按前后端纵向切片逐步接入。
      </Typography.Paragraph>
      <BackendStatus />
    </section>
  )
}

function RouteLoading() {
  return <div className="app-route-loading" aria-label="正在加载页面" />
}
