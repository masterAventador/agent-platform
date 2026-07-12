import { render, screen } from '@testing-library/react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { MemoryRouter } from 'react-router-dom'
import { describe, expect, it, vi } from 'vitest'

import { App } from './App'

vi.mock('../features/system/api/health', () => ({
  getHealth: vi.fn().mockResolvedValue({ status: 'ok' }),
}))

vi.mock('../features/auth/api/auth', () => ({
  getCurrentUser: vi.fn().mockResolvedValue({
    id: '00000000-0000-0000-0000-000000000001',
    email: 'owner@example.com',
    email_verified: false,
    workspaces: [
      {
        id: '00000000-0000-0000-0000-000000000010',
        name: 'owner 的工作区',
        slug: 'workspace-owner',
        role: 'owner',
      },
    ],
  }),
  logout: vi.fn().mockResolvedValue(undefined),
}))

describe('App', () => {
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
    expect(await screen.findByText('后端服务正常')).toBeInTheDocument()
  })
})
