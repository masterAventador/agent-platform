import { render, screen } from '@testing-library/react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { MemoryRouter } from 'react-router-dom'
import userEvent from '@testing-library/user-event'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import type { Workspace } from '../features/workspaces/types'
import { useWorkspaceStore } from '../features/workspaces/store'
import { App } from './App'

const ownerWorkspace: Workspace = {
  id: '00000000-0000-0000-0000-000000000010',
  name: 'Owner workspace',
  slug: 'workspace-owner',
  role: 'owner',
}
const adminWorkspace: Workspace = {
  id: '00000000-0000-0000-0000-000000000020',
  name: 'Admin workspace',
  slug: 'workspace-admin',
  role: 'admin',
}
const memberWorkspace: Workspace = {
  id: '00000000-0000-0000-0000-000000000030',
  name: 'Member workspace',
  slug: 'workspace-member',
  role: 'member',
}
const authState = vi.hoisted(() => ({ workspaces: [] as Workspace[] }))

vi.mock('../features/system/api/health', () => ({
  getHealth: vi.fn().mockResolvedValue({ status: 'ok' }),
}))

vi.mock('../features/auth/api/auth', () => ({
  getCurrentUser: vi.fn().mockImplementation(async () => ({
    id: '00000000-0000-0000-0000-000000000001',
    email: 'owner@example.com',
    email_verified: false,
    workspaces: authState.workspaces,
  })),
  logout: vi.fn().mockResolvedValue(undefined),
}))

describe('App', () => {
  beforeEach(() => {
    sessionStorage.clear()
    useWorkspaceStore.getState().clear()
    authState.workspaces = [ownerWorkspace]
  })

  it('展示数字员工平台的基础导航和后端状态', async () => {
    const queryClient = new QueryClient({
      defaultOptions: { queries: { retry: false } },
    })

    render(
      <QueryClientProvider client={queryClient}>
        <MemoryRouter>
          <App />
        </MemoryRouter>
      </QueryClientProvider>,
    )

    expect(
      await screen.findByRole('heading', { name: 'AI 数字员工平台' }),
    ).toBeInTheDocument()
    expect(screen.getByRole('link', { name: '工作台' })).toBeInTheDocument()
    expect(screen.getByRole('link', { name: '数字员工' })).toBeInTheDocument()
    expect(screen.getByRole('link', { name: '任务中心' })).toBeInTheDocument()
    expect(screen.getByRole('link', { name: 'Skill 中心' })).toBeInTheDocument()
    expect(screen.getByRole('link', { name: '工具与 MCP' })).toBeInTheDocument()
    expect(screen.getByRole('link', { name: '任务运维' })).toBeInTheDocument()
    expect(screen.getByLabelText('当前工作区').closest('.ant-select'))
      .toHaveTextContent('Owner workspace')
    expect(await screen.findByText('后端服务正常')).toBeInTheDocument()
  })

  it('普通成员不显示任务运维入口且直接访问时受控拒绝', async () => {
    authState.workspaces = [memberWorkspace]
    const queryClient = new QueryClient({
      defaultOptions: { queries: { retry: false } },
    })

    render(
      <QueryClientProvider client={queryClient}>
        <MemoryRouter initialEntries={['/operations/dead-letters']}>
          <App />
        </MemoryRouter>
      </QueryClientProvider>,
    )

    expect(await screen.findByText('无权访问死信管理')).toBeInTheDocument()
    expect(screen.queryByRole('link', { name: '任务运维' })).not.toBeInTheDocument()
  })

  it('管理员显示任务运维入口且可以直接访问', async () => {
    authState.workspaces = [adminWorkspace]
    const queryClient = new QueryClient({
      defaultOptions: { queries: { retry: false } },
    })

    render(
      <QueryClientProvider client={queryClient}>
        <MemoryRouter initialEntries={['/operations/dead-letters']}>
          <App />
        </MemoryRouter>
      </QueryClientProvider>,
    )

    expect(await screen.findByRole('link', { name: '任务运维' })).toBeInTheDocument()
    expect(await screen.findByRole('heading', { name: '死信管理' })).toBeInTheDocument()
    expect(screen.queryByText('无权访问死信管理')).not.toBeInTheDocument()
  })

  it('切换 active workspace 时清理旧租户查询、按新角色刷新导航并回到工作台', async () => {
    const user = userEvent.setup()
    authState.workspaces = [ownerWorkspace, memberWorkspace]
    const queryClient = new QueryClient({
      defaultOptions: { queries: { retry: false } },
    })
    queryClient.setQueryData(['employees', ownerWorkspace.id], [{ id: 'owner-only' }])
    const cancelQueries = vi.spyOn(queryClient, 'cancelQueries')

    render(
      <QueryClientProvider client={queryClient}>
        <MemoryRouter initialEntries={['/operations/dead-letters']}>
          <App />
        </MemoryRouter>
      </QueryClientProvider>,
    )

    expect(await screen.findByRole('heading', { name: '死信管理' })).toBeInTheDocument()
    await user.click(screen.getByLabelText('当前工作区'))
    await user.click(screen.getByText('Member workspace'))

    expect(await screen.findByRole('heading', { name: '工作台' })).toBeInTheDocument()
    expect(screen.queryByRole('link', { name: '任务运维' })).not.toBeInTheDocument()
    expect(queryClient.getQueryData(['employees', ownerWorkspace.id])).toBeUndefined()
    expect(cancelQueries).toHaveBeenCalled()
    expect(JSON.parse(sessionStorage.getItem('agent-platform.active-workspace')!)).toEqual({
      user_id: '00000000-0000-0000-0000-000000000001',
      workspace_id: memberWorkspace.id,
    })
  })

  it('active admin workspace 而非第一 workspace 决定权限入口', async () => {
    const user = userEvent.setup()
    authState.workspaces = [memberWorkspace, adminWorkspace]
    const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } })

    render(
      <QueryClientProvider client={queryClient}>
        <MemoryRouter><App /></MemoryRouter>
      </QueryClientProvider>,
    )

    expect((await screen.findByLabelText('当前工作区')).closest('.ant-select'))
      .toHaveTextContent('Member workspace')
    expect(screen.queryByRole('link', { name: '任务运维' })).not.toBeInTheDocument()
    await user.click(screen.getByLabelText('当前工作区'))
    await user.click(screen.getByText('Admin workspace'))
    expect(await screen.findByRole('link', { name: '任务运维' })).toBeInTheDocument()
  })

  it('没有 workspace 时显示受控空态且不渲染租户功能', async () => {
    authState.workspaces = []
    const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } })

    render(
      <QueryClientProvider client={queryClient}>
        <MemoryRouter><App /></MemoryRouter>
      </QueryClientProvider>,
    )

    expect(await screen.findByText('暂无可用工作区')).toBeInTheDocument()
    expect(screen.queryByRole('link', { name: '数字员工' })).not.toBeInTheDocument()
  })

  it.each([
    ['member', memberWorkspace, '/employees/employee-1/edit'],
    ['admin', adminWorkspace, '/employees/new'],
  ])('%s 直接访问 owner-only 员工编辑路由时显示受控 403', async (_, workspace, path) => {
    authState.workspaces = [workspace]
    const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } })

    render(
      <QueryClientProvider client={queryClient}>
        <MemoryRouter initialEntries={[path]}><App /></MemoryRouter>
      </QueryClientProvider>,
    )

    expect(await screen.findByText('无权编辑数字员工')).toBeInTheDocument()
    expect(screen.queryByRole('heading', { name: /创建数字员工|编辑数字员工/ }))
      .not.toBeInTheDocument()
  })
})
