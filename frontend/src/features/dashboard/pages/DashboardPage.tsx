import { Alert, Button, Card, Flex, Spin, Statistic, Tag, theme, Typography } from 'antd'

import type { RunStatus } from '../../runs/api/runs'
import { runStatusLabels } from '../../runs/pages/status'
import { BackendStatus } from '../../system/components/BackendStatus'
import { useWorkbenchSummary } from '../api/queries'
import type { WorkbenchSummary } from '../api/workbench'
import './dashboard.css'


const displayedRunStatuses: readonly RunStatus[] = [
  'queued',
  'running',
  'waiting_for_input',
  'waiting_for_approval',
  'completed',
  'failed',
  'cancelled',
]

const runStatusAccessibleNames: Record<RunStatus, string> = {
  queued: '排队中任务',
  running: '执行中任务',
  waiting_for_input: '等待输入任务',
  waiting_for_approval: '等待审批任务',
  completed: '已完成任务',
  failed: '失败任务',
  cancelled: '已取消任务',
}

export function DashboardPage() {
  const summary = useWorkbenchSummary()

  return (
    <section>
      <Typography.Title level={2}>工作台</Typography.Title>
      <Typography.Paragraph type="secondary">
        查看当前工作区的数字员工、任务状态与平台健康状况。
      </Typography.Paragraph>
      <div className="dashboard-health">
        <BackendStatus />
      </div>
      {summary.isPending ? (
        <Flex className="dashboard-loading" justify="center" aria-label="正在加载工作台数据">
          <Spin />
        </Flex>
      ) : summary.isError || summary.data === undefined ? (
        <Alert
          type="error"
          showIcon
          title="工作台数据加载失败"
          description="真实统计暂时不可用，请稍后重试。"
          action={<Button onClick={() => void summary.refetch()}>重新加载</Button>}
        />
      ) : (
        <DashboardSummary summary={summary.data} />
      )}
    </section>
  )
}

function DashboardSummary({ summary }: { summary: WorkbenchSummary }) {
  const { token } = theme.useToken()

  return (
    <>
      <div className="dashboard-summary-grid">
        <Card>
          <Statistic title="数字员工" value={summary.employees.total} aria-label="数字员工总数" />
          <Flex className="dashboard-card-breakdown" gap={20} wrap>
            <Statistic
              title="已发布"
              value={summary.employees.published}
              aria-label="已发布员工"
            />
            <Statistic title="草稿" value={summary.employees.draft} aria-label="草稿员工" />
          </Flex>
        </Card>
        <Card>
          <Statistic title="任务总数" value={summary.runs.total} aria-label="任务总数" />
          <Typography.Text type="secondary">统计范围遵循当前用户的数据权限</Typography.Text>
        </Card>
        <Card style={summary.runs.failed > 0 ? { borderColor: token.colorErrorBorder } : undefined}>
          <Statistic
            title="失败任务"
            value={summary.runs.failed}
            styles={summary.runs.failed > 0 ? { content: { color: token.colorError } } : undefined}
            aria-label="失败任务总数"
          />
          <Typography.Text type={summary.runs.failed > 0 ? 'danger' : 'secondary'}>
            {summary.runs.failed > 0 ? '有任务需要关注' : '当前没有失败任务'}
          </Typography.Text>
        </Card>
      </div>
      <Card className="dashboard-status-card" title="任务状态分布">
        <div className="dashboard-status-grid">
          {displayedRunStatuses.map((status) => (
            <Flex
              key={status}
              className="dashboard-status-item"
              align="center"
              justify="space-between"
              aria-label={runStatusAccessibleNames[status]}
              style={{
                background: token.colorFillQuaternary,
                borderRadius: token.borderRadius,
              }}
            >
              <Tag color={runStatusLabels[status].color}>{runStatusLabels[status].text}</Tag>
              <Typography.Text strong>{summary.runs[status]}</Typography.Text>
            </Flex>
          ))}
        </div>
      </Card>
    </>
  )
}
