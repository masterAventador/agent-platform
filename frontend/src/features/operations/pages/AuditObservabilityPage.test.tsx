import { render, screen } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import { useAuditEvents } from '../api/queries'
import { AuditObservabilityPage } from './AuditObservabilityPage'


vi.mock('../api/queries', () => ({ useAuditEvents: vi.fn() }))

const refetch = vi.fn()
const auditEvents = [
  {
    id: '30000000-0000-4000-8000-000000000030',
    tenant_id: '10000000-0000-4000-8000-000000000010',
    actor_user_id: '20000000-0000-4000-8000-000000000020',
    sequence: 2,
    action: 'employee.created',
    resource_type: 'employee',
    resource_id: '40000000-0000-4000-8000-000000000040',
    outcome: 'succeeded',
    correlation_id: 'run-correlation-1',
    previous_hash: 'a'.repeat(64),
    event_hash: 'b'.repeat(64),
    metadata: { runtime_type: 'flow' },
    occurred_at: '2026-07-16T08:00:00Z',
  },
]

describe('AuditObservabilityPage', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    vi.mocked(useAuditEvents).mockReturnValue({
      data: auditEvents,
      isPending: false,
      isError: false,
      error: null,
      refetch,
    } as unknown as ReturnType<typeof useAuditEvents>)
  })

  it('展示审计事件、观测入口和故障定位字段', () => {
    render(<AuditObservabilityPage />)

    expect(screen.getByRole('heading', { name: '审计与观测' })).toBeInTheDocument()
    expect(screen.getByText('employee.created')).toBeInTheDocument()
    expect(screen.getByText('employee')).toBeInTheDocument()
    expect(screen.getByText('run-correlation-1')).toBeInTheDocument()
    expect(screen.getByRole('link', { name: '打开 Jaeger 本机链路追踪' })).toHaveAttribute(
      'href',
      'http://127.0.0.1:16686/',
    )
  })

  it('加载中、失败和空列表都不伪造成功状态', () => {
    vi.mocked(useAuditEvents).mockReturnValue({
      data: undefined,
      isPending: true,
      isError: false,
      error: null,
      refetch,
    } as unknown as ReturnType<typeof useAuditEvents>)
    const { rerender } = render(<AuditObservabilityPage />)

    expect(screen.getByLabelText('正在加载审计事件')).toBeInTheDocument()

    vi.mocked(useAuditEvents).mockReturnValue({
      data: undefined,
      isPending: false,
      isError: true,
      error: new Error('failed'),
      refetch,
    } as unknown as ReturnType<typeof useAuditEvents>)
    rerender(<AuditObservabilityPage />)
    expect(screen.getByText('审计事件加载失败')).toBeInTheDocument()

    vi.mocked(useAuditEvents).mockReturnValue({
      data: [],
      isPending: false,
      isError: false,
      error: null,
      refetch,
    } as unknown as ReturnType<typeof useAuditEvents>)
    rerender(<AuditObservabilityPage />)
    expect(screen.getByText('当前没有审计事件')).toBeInTheDocument()
  })
})
