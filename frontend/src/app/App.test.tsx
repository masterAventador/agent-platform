import { render, screen } from '@testing-library/react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { MemoryRouter } from 'react-router-dom'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import { App } from './App'

const authState = vi.hoisted(() => ({ role: 'owner' as 'owner' | 'admin' | 'member' }))

vi.mock('../features/system/api/health', () => ({
  getHealth: vi.fn().mockResolvedValue({ status: 'ok' }),
}))

vi.mock('../features/auth/api/auth', () => ({
  getCurrentUser: vi.fn().mockImplementation(async () => ({
    id: '00000000-0000-0000-0000-000000000001',
    email: 'owner@example.com',
    email_verified: false,
    workspaces: [
      {
        id: '00000000-0000-0000-0000-000000000010',
        name: 'owner 的工作区',
        slug: 'workspace-owner',
        role: authState.role,
      },
    ],
  })),
  logout: vi.fn().mockResolvedValue(undefined),
}))

describe('App', () => {
  beforeEach(() => {
    authState.role = 'owner'
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
    expect(await screen.findByText('后端服务正常')).toBeInTheDocument()
  })

  it('普通成员不显示任务运维入口且直接访问时受控拒绝', async () => {
    authState.role = 'member'
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
    authState.role = 'admin'
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
})
