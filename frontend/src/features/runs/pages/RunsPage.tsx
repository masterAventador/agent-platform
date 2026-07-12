import { Card, Empty, Flex, Space, Spin, Tag, Typography } from 'antd'
import { useNavigate } from 'react-router-dom'

import { useRuns } from '../api/queries'
import { formatRunInput, runStatusLabels } from './status'
import './runs.css'


export function RunsPage() {
  const runs = useRuns()
  const navigate = useNavigate()

  return (
    <section>
      <Typography.Title level={2}>任务中心</Typography.Title>
      <Typography.Text type="secondary">集中查看数字员工任务的状态与执行过程</Typography.Text>
      {runs.isPending ? (
        <Flex className="run-loading" justify="center"><Spin /></Flex>
      ) : runs.data?.length ? (
        <div className="run-list">
          {runs.data.map((run) => {
            const status = runStatusLabels[run.status]
            return (
              <Card
                key={run.id}
                hoverable
                className="run-card"
                onClick={() => navigate(`/runs/${run.id}`)}
              >
                <Flex align="center" justify="space-between" gap={16}>
                  <Space orientation="vertical" size={4}>
                    <Typography.Text strong>任务 {run.id.slice(0, 8)}</Typography.Text>
                    <Typography.Text type="secondary">
                      数字员工版本 {run.employee_version} · {formatRunInput(run.input)}
                    </Typography.Text>
                  </Space>
                  <Tag color={status.color}>{status.text}</Tag>
                </Flex>
              </Card>
            )
          })}
        </div>
      ) : (
        <Card className="run-empty"><Empty description="还没有任务" /></Card>
      )}
    </section>
  )
}
