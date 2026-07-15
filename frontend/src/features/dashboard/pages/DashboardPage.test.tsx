import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { ConfigProvider } from 'antd'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import { useWorkbenchSummary } from '../api/queries'
import { DashboardPage } from './DashboardPage'


vi.mock('../api/queries', () => ({
  useWorkbenchSummary: vi.fn(),
}))

vi.mock('../../system/components/BackendStatus', () => ({
  BackendStatus: () => <div>后端服务正常</div>,
}))

const refetch = vi.fn()
const summary = {
  employees: { total: 2, draft: 1, published: 1 },
  runs: {
    total: 7,
    queued: 1,
    running: 1,
    waiting_for_input: 1,
    waiting_for_approval: 1,
    completed: 1,
    failed: 1,
    cancelled: 1,
  },
}

describe('DashboardPage', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    vi.mocked(useWorkbenchSummary).mockReturnValue({
      data: summary,
      isPending: false,
      isError: false,
      refetch,
    } as unknown as ReturnType<typeof useWorkbenchSummary>)
  })

  it('展示真实员工、全部任务状态、失败任务和系统健康数据', () => {
    render(<DashboardPage />)

    expect(screen.getByRole('heading', { name: '工作台' })).toBeInTheDocument()
    expect(screen.getByLabelText('数字员工总数')).toHaveTextContent('2')
    expect(screen.getByLabelText('已发布员工')).toHaveTextContent('1')
    expect(screen.getByLabelText('草稿员工')).toHaveTextContent('1')
    expect(screen.getByLabelText('任务总数')).toHaveTextContent('7')
    expect(screen.getByLabelText('失败任务总数')).toHaveTextContent('1')
    expect(screen.getByLabelText('排队中任务')).toHaveTextContent('1')
    expect(screen.getByLabelText('执行中任务')).toHaveTextContent('1')
    expect(screen.getByLabelText('等待输入任务')).toHaveTextContent('1')
    expect(screen.getByLabelText('等待审批任务')).toHaveTextContent('1')
    expect(screen.getByLabelText('已完成任务')).toHaveTextContent('1')
    expect(screen.getByLabelText('已取消任务')).toHaveTextContent('1')
    expect(screen.getByText('后端服务正常')).toBeInTheDocument()
    expect(screen.queryByText(/产物|模型用量/)).not.toBeInTheDocument()
  })

  it('将主题 token 作为真实样式应用到失败状态和任务状态项', () => {
    const token = {
      colorErrorBorder: '#a10b0b',
      colorError: '#b20c0c',
      colorFillQuaternary: '#c30d0d',
      borderRadius: 13,
    }

    render(
      <ConfigProvider theme={{ token }}>
        <DashboardPage />
      </ConfigProvider>,
    )

    const failureStatistic = screen.getByLabelText('失败任务总数')
    const failureCard = failureStatistic.closest('.ant-card')
    const failureValue = failureStatistic.querySelector('.ant-statistic-content')
    const queuedStatus = screen.getByLabelText('排队中任务')

    expect(failureCard).toHaveStyle({ borderColor: token.colorErrorBorder })
    expect(failureValue).toHaveStyle({ color: token.colorError })
    expect(queuedStatus).toHaveStyle({
      background: token.colorFillQuaternary,
      borderRadius: `${token.borderRadius}px`,
    })
  })

  it('加载失败时不伪装成全零数据且允许重试', async () => {
    const user = userEvent.setup()
    refetch.mockResolvedValue(undefined)
    vi.mocked(useWorkbenchSummary).mockReturnValue({
      data: undefined,
      isPending: false,
      isError: true,
      refetch,
    } as unknown as ReturnType<typeof useWorkbenchSummary>)

    render(<DashboardPage />)

    expect(screen.getByText('工作台数据加载失败')).toBeInTheDocument()
    expect(screen.queryByLabelText('任务总数')).not.toBeInTheDocument()
    await user.click(screen.getByRole('button', { name: '重新加载' }))
    expect(refetch).toHaveBeenCalledTimes(1)
  })

  it('加载期间显示明确状态并继续展示独立系统健康', () => {
    vi.mocked(useWorkbenchSummary).mockReturnValue({
      data: undefined,
      isPending: true,
      isError: false,
      refetch,
    } as unknown as ReturnType<typeof useWorkbenchSummary>)

    render(<DashboardPage />)

    expect(screen.getByLabelText('正在加载工作台数据')).toBeInTheDocument()
    expect(screen.getByText('后端服务正常')).toBeInTheDocument()
  })
})
