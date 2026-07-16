import { useIsMutating, useQueryClient } from '@tanstack/react-query'
import { Alert, Button, Flex, Layout, Result, Select, Space, Typography } from 'antd'
import { lazy, Suspense, useEffect, useRef, useState } from 'react'
import { Link, Route, Routes, useNavigate } from 'react-router-dom'

import { isTenantMutationFor } from '../api/tenant'
import { useCurrentUser, useLogout } from '../features/auth/api/queries'
import type { CurrentUser } from '../features/auth/api/auth'
import { ProtectedRoute } from '../features/auth/components/ProtectedRoute'
import { WorkspaceCapabilityGate } from '../features/workspaces/components/WorkspaceCapabilityGate'
import {
  getWorkspaceCapabilities,
  workspacePermissions,
} from '../features/workspaces/permissions'
import { useWorkspaceSelection } from '../features/workspaces/store'
import { useCapabilityRegistry } from './capability-registry/queries'
import type { FrontendCapabilityDescriptor } from './capability-registry/modules'
import type { CapabilityAccess } from './capability-registry/registry'
import type { FrontendCapabilityModule } from './capability-registry/types'
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
const DashboardPage = lazy(() =>
  import('../features/dashboard/pages/DashboardPage').then((module) => ({
    default: module.DashboardPage,
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
const ConversationsPage = lazy(() =>
  import('../features/conversations/pages/ConversationsPage').then((module) => ({
    default: module.ConversationsPage,
  })),
)
const ConversationDetailPage = lazy(() =>
  import('../features/conversations/pages/ConversationDetailPage').then((module) => ({
    default: module.ConversationDetailPage,
  })),
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
const ToolsPage = lazy(() =>
  import('../features/tools/pages/ToolsPage').then((module) => ({
    default: module.ToolsPage,
  })),
)
const DeadLettersPage = lazy(() =>
  import('../features/operations/pages/DeadLettersPage').then((module) => ({
    default: module.DeadLettersPage,
  })),
)
const AuditObservabilityPage = lazy(() =>
  import('../features/operations/pages/AuditObservabilityPage').then((module) => ({
    default: module.AuditObservabilityPage,
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

  if (currentUser.data === undefined) return <RouteLoading />

  return <AuthenticatedPlatformShell user={currentUser.data} />
}

function AuthenticatedPlatformShell({ user }: { user: CurrentUser }) {
  const logout = useLogout()
  const navigate = useNavigate()
  const queryClient = useQueryClient()
  const { activeWorkspace, isReconciled, select } = useWorkspaceSelection(user)
  const capabilityRegistry = useCapabilityRegistry(
    activeWorkspace?.id,
    activeWorkspace?.permissions ?? [],
  )
  const [workspaceSwitchWarning, setWorkspaceSwitchWarning] = useState<string>()
  const [isWorkspaceSwitching, setIsWorkspaceSwitching] = useState(false)
  const workspaceSwitchInFlight = useRef(false)
  const pendingActiveWorkspaceMutations = useIsMutating({
    predicate: (mutation) => activeWorkspace !== undefined
      && isTenantMutationFor(mutation.options.mutationKey, activeWorkspace.id),
  })

  useEffect(() => {
    if (pendingActiveWorkspaceMutations === 0) setWorkspaceSwitchWarning(undefined)
  }, [pendingActiveWorkspaceMutations])

  const signOut = async () => {
    await logout.mutateAsync()
    navigate('/login', { replace: true })
  }

  if (!isReconciled) return <RouteLoading />

  if (activeWorkspace === undefined) {
    return (
      <Layout className="app-shell">
        <Content className="app-empty-workspace">
          <Result
            status="info"
            title="暂无可用工作区"
            subTitle="你的账号当前未加入任何工作区，请联系管理员后重试。"
            extra={(
              <Button loading={logout.isPending} onClick={signOut}>
                退出登录
              </Button>
            )}
          />
        </Content>
      </Layout>
    )
  }

  const capabilities = getWorkspaceCapabilities(activeWorkspace)
  const capabilityRegistryPending = capabilityRegistry.registry.isPending
    || (capabilityRegistry.registry.isSuccess && capabilityRegistry.modules.isPending)
  const capabilityRegistryError = capabilityRegistry.registry.isError
    || capabilityRegistry.modules.isError
  const registeredCapabilities = capabilityRegistry.registry.data?.capabilities ?? []
  const capabilityModules = capabilityRegistry.modules.data ?? []
  const availableModules = capabilityModules.filter(
    (entry): entry is typeof entry & {
      access: 'allowed'
      descriptor: FrontendCapabilityDescriptor
      module: FrontendCapabilityModule
    } => (
      entry.access === 'allowed' && entry.module !== undefined && entry.descriptor !== undefined
    ),
  )

  const switchWorkspace = async (workspaceId: string) => {
    if (workspaceId === activeWorkspace.id || workspaceSwitchInFlight.current) return

    workspaceSwitchInFlight.current = true
    setIsWorkspaceSwitching(true)

    const previousWorkspaceId = activeWorkspace.id
    const hasPendingMutation = () => queryClient.isMutating({
      predicate: (mutation) => isTenantMutationFor(
        mutation.options.mutationKey,
        previousWorkspaceId,
      ),
    }) > 0

    try {
      if (hasPendingMutation()) {
        setWorkspaceSwitchWarning('当前工作区仍有操作正在提交，请等待完成后再切换。')
        return
      }

      const belongsToPreviousWorkspace = (queryKey: readonly unknown[]) => (
        queryKey.includes(previousWorkspaceId)
      )

      await queryClient.cancelQueries({
        predicate: (query) => belongsToPreviousWorkspace(query.queryKey),
      })
      if (hasPendingMutation()) {
        setWorkspaceSwitchWarning('当前工作区仍有操作正在提交，请等待完成后再切换。')
        return
      }

      queryClient.removeQueries({
        predicate: (query) => belongsToPreviousWorkspace(query.queryKey),
      })
      if (select(workspaceId)) {
        setWorkspaceSwitchWarning(undefined)
        navigate('/', { replace: true })
      }
    } finally {
      workspaceSwitchInFlight.current = false
      setIsWorkspaceSwitching(false)
    }
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
            {capabilities.canExecuteRuns && <Link to="/runs">任务中心</Link>}
            {capabilities.canExecuteRuns && <Link to="/conversations">会话中心</Link>}
            <Link to="/knowledge-bases">知识库</Link>
            <Link to="/skills">Skill 中心</Link>
            {capabilities.canManageTools && <Link to="/tools">工具与 MCP</Link>}
            {capabilities.canManageOperations && (
              <>
                <Link to="/operations/dead-letters">任务运维</Link>
                <Link to="/operations/audit-observability">审计与观测</Link>
              </>
            )}
            {availableModules.flatMap(({ descriptor }) => descriptor.navigation).map((entry) => (
              <Link key={entry.path} to={entry.path}>{entry.label}</Link>
            ))}
          </Space>
        </nav>
      </Sider>
      <Content className="app-content">
        <Flex className="app-topbar" align="center" justify="space-between" gap={16}>
          <Select
            aria-label="当前工作区"
            className="app-workspace-select"
            disabled={isWorkspaceSwitching}
            value={activeWorkspace.id}
            options={user.workspaces.map((workspace) => ({
              value: workspace.id,
              label: workspace.name,
            }))}
            onChange={(workspaceId) => void switchWorkspace(workspaceId)}
          />
          <Flex align="center" gap={16}>
            <Typography.Text type="secondary">{user.email}</Typography.Text>
            <Button loading={logout.isPending} onClick={signOut}>
              退出登录
            </Button>
          </Flex>
        </Flex>
        {workspaceSwitchWarning && (
          <Alert type="warning" showIcon title={workspaceSwitchWarning} />
        )}
        <Routes>
          <Route path="/" element={<DashboardPage />} />
          <Route
            path="/employees"
            element={<EmployeesPage canManageEmployees={capabilities.canManageEmployees} />}
          />
          <Route
            path="/employees/new"
            element={(
              <WorkspaceCapabilityGate
                workspace={activeWorkspace}
                permission={workspacePermissions.employeesManage}
                title="无权编辑数字员工"
              >
                <EmployeeEditorPage />
              </WorkspaceCapabilityGate>
            )}
          />
          <Route
            path="/employees/:employeeId"
            element={(
              <EmployeeDetailPage
                canManageEmployees={capabilities.canManageEmployees}
                canExecuteRuns={capabilities.canExecuteRuns}
              />
            )}
          />
          <Route
            path="/employees/:employeeId/edit"
            element={(
              <WorkspaceCapabilityGate
                workspace={activeWorkspace}
                permission={workspacePermissions.employeesManage}
                title="无权编辑数字员工"
              >
                <EmployeeEditorPage />
              </WorkspaceCapabilityGate>
            )}
          />
          <Route
            path="/runs"
            element={(
              <WorkspaceCapabilityGate
                workspace={activeWorkspace}
                permission={workspacePermissions.runsExecute}
                title="无权访问任务中心"
              >
                <RunsPage />
              </WorkspaceCapabilityGate>
            )}
          />
          <Route
            path="/runs/:runId"
            element={(
              <WorkspaceCapabilityGate
                workspace={activeWorkspace}
                permission={workspacePermissions.runsExecute}
                title="无权访问任务中心"
              >
                <RunDetailPage
                  canExecuteRuns={capabilities.canExecuteRuns}
                  canManageRuns={capabilities.canManageRuns}
                />
              </WorkspaceCapabilityGate>
            )}
          />
          <Route
            path="/conversations"
            element={(
              <WorkspaceCapabilityGate
                workspace={activeWorkspace}
                permission={workspacePermissions.runsExecute}
                title="无权访问会话中心"
              >
                <ConversationsPage />
              </WorkspaceCapabilityGate>
            )}
          />
          <Route
            path="/conversations/:conversationId"
            element={(
              <WorkspaceCapabilityGate
                workspace={activeWorkspace}
                permission={workspacePermissions.runsExecute}
                title="无权访问会话中心"
              >
                <ConversationDetailPage />
              </WorkspaceCapabilityGate>
            )}
          />
          <Route
            path="/knowledge-bases"
            element={(
              <KnowledgeBasesPage canManageKnowledge={capabilities.canManageKnowledge} />
            )}
          />
          <Route
            path="/knowledge-bases/:knowledgeBaseId"
            element={(
              <KnowledgeBaseDetailPage canManageKnowledge={capabilities.canManageKnowledge} />
            )}
          />
          <Route
            path="/skills"
            element={<SkillsPage canManageSkills={capabilities.canManageSkills} />}
          />
          <Route
            path="/skills/:skillId"
            element={<SkillDetailPage canManageSkills={capabilities.canManageSkills} />}
          />
          <Route
            path="/tools"
            element={(
              <WorkspaceCapabilityGate
                workspace={activeWorkspace}
                permission={workspacePermissions.toolsManage}
                title="无权访问工具与 MCP"
              >
                <ToolsPage canManageTools={capabilities.canManageTools} />
              </WorkspaceCapabilityGate>
            )}
          />
          <Route
            path="/operations/dead-letters"
            element={(
              <WorkspaceCapabilityGate
                workspace={activeWorkspace}
                permission={workspacePermissions.operationsManage}
                title="无权访问死信管理"
              >
                <DeadLettersPage />
              </WorkspaceCapabilityGate>
            )}
          />
          <Route
            path="/operations/audit-observability"
            element={(
              <WorkspaceCapabilityGate
                workspace={activeWorkspace}
                permission={workspacePermissions.operationsManage}
                title="无权访问审计与观测"
              >
                <AuditObservabilityPage />
              </WorkspaceCapabilityGate>
            )}
          />
          {capabilityModules.flatMap(({ access, descriptor, module }) => (
            descriptor?.routePaths.map((path) => {
              const Page = module?.routes.find((route) => route.path === path)?.Page
              return (
              <Route
                key={path}
                path={path}
                element={access === 'allowed' && Page !== undefined
                  ? <Page workspaceId={activeWorkspace.id} />
                  : <CapabilityAccessDenied access={access === 'allowed' ? 'incompatible' : access} />}
              />
              )
            }) ?? []
          ))}
          <Route
            path="*"
            element={capabilityRegistryPending
              ? <RouteLoading />
              : capabilityRegistryError
                ? <Result status="error" title="无法确认能力授权" />
                : (
              <CapabilityAccessDenied
                access={registeredCapabilities.length === 0 ? 'not-installed' : 'incompatible'}
              />
                )}
          />
        </Routes>
      </Content>
    </Layout>
  )
}

function CapabilityAccessDenied({ access }: { access: Exclude<CapabilityAccess, 'allowed'> }) {
  const title = {
    forbidden: '无权访问此能力',
    incompatible: '能力清单与客户端模块不兼容',
    'not-entitled': '当前工作区未获此能力授权',
    'not-installed': '当前部署未安装此能力',
  }[access]
  return <Result status="403" title={title} />
}

function RouteLoading() {
  return <div className="app-route-loading" aria-label="正在加载页面" />
}
