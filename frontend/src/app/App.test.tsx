import { render, screen } from '@testing-library/react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { MemoryRouter } from 'react-router-dom'
import { describe, expect, it, vi } from 'vitest'

import { App } from './App'

vi.mock('../features/system/api/health', () => ({
  getHealth: vi.fn().mockResolvedValue({ status: 'ok' }),
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

    expect(screen.getByRole('heading', { name: 'AI 数字员工平台' })).toBeInTheDocument()
    expect(screen.getByRole('link', { name: '工作台' })).toBeInTheDocument()
    expect(screen.getByRole('link', { name: '数字员工' })).toBeInTheDocument()
    expect(screen.getByRole('link', { name: '任务中心' })).toBeInTheDocument()
    expect(await screen.findByText('后端服务正常')).toBeInTheDocument()
  })
})
