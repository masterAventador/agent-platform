import {
  Alert,
  Button,
  Card,
  Descriptions,
  Empty,
  Flex,
  Space,
  Spin,
  Table,
  Tag,
  Typography,
} from 'antd'
import { Link, useParams } from 'react-router-dom'

import { useScheduledTask, useScheduledTaskExecutions } from '../api/queries'
import type { ScheduledTaskExecution } from '../api/scheduled-tasks'
import {
  describeSchedule,
  describeScheduledTaskError,
  executionStatusLabels,
  pauseReasonLabels,
  skipReasonLabels,
} from './schedule-display'
import { EMPTY_PLACEHOLDER, formatInstantInTimezone } from './zoned-time'


export function ScheduledTaskDetailPage() {
  const { taskId } = useParams<{ taskId: string }>()
  const task = useScheduledTask(taskId)
  const executions = useScheduledTaskExecutions(taskId)

  if (task.isPending) {
    return <Flex justify="center" aria-label="正在加载定时任务"><Spin /></Flex>
  }
  if (task.isError || task.data === undefined) {
    // 越权访问按 404 处理（与 runs 语义一致），统一转成用户可读提示。
    return <Alert type="error" showIcon title={describeScheduledTaskError(task.error)} />
  }

  const detail = task.data
  const timezone = detail.schedule.timezone

  return (
    <section>
      <Flex align="center" justify="space-between" gap={16}>
        <div>
          <Space align="center">
            <Typography.Title level={2}>{detail.name}</Typography.Title>
            <Tag color={detail.enabled ? 'success' : 'default'}>
              {detail.enabled ? '启用中' : '已暂停'}
            </Tag>
          </Space>
          {!detail.enabled && detail.pause_reason && (
            <Typography.Text type="warning">
              {pauseReasonLabels[detail.pause_reason] ?? detail.pause_reason}
            </Typography.Text>
          )}
        </div>
        <Link to="/scheduled-tasks">返回定时任务中心</Link>
      </Flex>
      <section aria-label="任务概览">
        <Descriptions
          column={2}
          items={[
            { key: 'schedule', label: '调度', children: describeSchedule(detail.schedule) },
            {
              key: 'next',
              label: '下次执行时间',
              // 全部时间都按任务自己的时区渲染，与调度语义保持一致。
              children: formatInstantInTimezone(detail.next_run_at, timezone),
            },
            {
              key: 'last',
              label: '上次执行时间',
              children: formatInstantInTimezone(detail.last_run_at, timezone),
            },
            {
              key: 'policies',
              label: '策略',
              children: `错过：${detail.misfire_policy} · 并发：${detail.concurrency_policy}`
                + ` · 重试：${detail.max_retries} 次 / 退避 ${detail.retry_backoff_seconds} 秒`,
            },
          ]}
        />
      </section>
      <section aria-label="执行记录">
        <Typography.Title level={3}>执行记录</Typography.Title>
        {executions.isPending ? (
          <Flex justify="center" aria-label="正在加载执行记录"><Spin /></Flex>
        ) : executions.isError || executions.data === undefined ? (
          <Alert
            type="error"
            showIcon
            title="执行记录加载失败"
            action={<Button onClick={() => void executions.refetch()}>重新加载</Button>}
          />
        ) : executions.data.items.length === 0 ? (
          <Card><Empty description="还没有执行记录" /></Card>
        ) : (
          <Table<ScheduledTaskExecution>
            rowKey="id"
            dataSource={executions.data.items}
            pagination={false}
            columns={[
              {
                title: '触发时间',
                key: 'scheduled_for',
                render: (_, item) => formatInstantInTimezone(item.scheduled_for, timezone),
              },
              {
                title: '状态',
                key: 'status',
                render: (_, item) => {
                  const status = executionStatusLabels[item.status]
                  return <Tag color={status.color}>{status.text}</Tag>
                },
              },
              { title: '尝试次数', key: 'attempts', render: (_, item) => item.attempts },
              {
                title: '关联任务',
                key: 'run',
                render: (_, item) => (
                  item.run_id === null
                    ? EMPTY_PLACEHOLDER
                    : <Link to={`/runs/${item.run_id}`}>查看任务</Link>
                ),
              },
              {
                title: '说明',
                key: 'reason',
                render: (_, item) => {
                  if (item.skip_reason) {
                    return skipReasonLabels[item.skip_reason] ?? item.skip_reason
                  }
                  return item.error_message ?? EMPTY_PLACEHOLDER
                },
              },
              {
                title: '下次重试',
                key: 'next_attempt_at',
                render: (_, item) => formatInstantInTimezone(item.next_attempt_at, timezone),
              },
            ]}
          />
        )}
      </section>
    </section>
  )
}
