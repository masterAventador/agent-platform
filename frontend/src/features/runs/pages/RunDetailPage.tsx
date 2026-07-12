import { useQueryClient } from '@tanstack/react-query'
import { Button, Card, Descriptions, Empty, Flex, Space, Spin, Tag, Typography } from 'antd'
import { useEffect } from 'react'
import { useParams } from 'react-router-dom'

import { useActiveWorkspaceId } from '../../employees/api/queries'
import { runKeys, useControlRun, useRun, useRunEvents } from '../api/queries'
import { formatRunInput, runStatusLabels } from './status'
import './runs.css'


const terminalStatuses = new Set(['completed', 'failed', 'cancelled'])
const streamEvents = [
  'run.started', 'run.progress', 'message.output', 'approval.required',
  'run.completed', 'run.failed', 'run.cancelled',
]

export function RunDetailPage() {
  const { runId } = useParams()
  const run = useRun(runId)
  const events = useRunEvents(runId)
  const control = useControlRun(runId ?? '')
  const tenantId = useActiveWorkspaceId()
  const queryClient = useQueryClient()

  useEffect(() => {
    if (!runId || !tenantId || !run.data || terminalStatuses.has(run.data.status)) return
    const source = new EventSource(`/api/v1/runs/${runId}/stream`, { withCredentials: true })
    const refresh = () => {
      void queryClient.invalidateQueries({ queryKey: runKeys.detail(tenantId, runId) })
      void queryClient.invalidateQueries({ queryKey: runKeys.events(tenantId, runId) })
    }
    streamEvents.forEach((type) => source.addEventListener(type, refresh))
    return () => source.close()
  }, [queryClient, run.data, runId, tenantId])

  if (run.isPending || !run.data) {
    return <Flex className="run-loading" justify="center"><Spin /></Flex>
  }
  const data = run.data
  const status = runStatusLabels[data.status]
  const cancellable = !terminalStatuses.has(data.status)
  const approvalId = [...(events.data ?? [])]
    .reverse()
    .find((event) => event.type === 'approval.required')?.payload.approval_id

  return (
    <section className="run-detail">
      <Flex align="center" justify="space-between" gap={16}>
        <Space align="center">
          <Typography.Title level={2}>任务详情</Typography.Title>
          <Tag color={status.color}>{status.text}</Tag>
        </Space>
        <Space>
          {data.status === 'waiting_for_input' && (
            <Button
              type="primary"
              loading={control.isPending}
              onClick={() => control.mutate({ action: 'resume' })}
            >
              继续执行
            </Button>
          )}
          {data.status === 'waiting_for_approval' && typeof approvalId === 'string' && (
            <>
              <Button
                type="primary"
                loading={control.isPending}
                onClick={() => control.mutate({ action: 'approve', approvalId })}
              >
                批准
              </Button>
              <Button
                danger
                loading={control.isPending}
                onClick={() => control.mutate({ action: 'reject', approvalId })}
              >
                拒绝
              </Button>
            </>
          )}
          {cancellable && (
            <Button
              danger
              loading={control.isPending}
              onClick={() => control.mutate({ action: 'cancel' })}
            >
              取消任务
            </Button>
          )}
        </Space>
      </Flex>
      <Card className="run-summary" title="任务信息">
        <Descriptions column={2}>
          <Descriptions.Item label="任务 ID">{data.id}</Descriptions.Item>
          <Descriptions.Item label="员工版本">版本 {data.employee_version}</Descriptions.Item>
          <Descriptions.Item label="任务内容" span={2}>
            <Typography.Text className="run-input">{formatRunInput(data.input)}</Typography.Text>
          </Descriptions.Item>
        </Descriptions>
      </Card>
      <Card className="run-events" title="执行动态">
        {events.data?.length ? (
          <div className="run-event-list">
            {events.data.map((event) => (
              <div className="run-event-item" key={event.event_id}>
              <Space orientation="vertical" size={2}>
                <Typography.Text strong>{eventLabel(event.type, event.payload)}</Typography.Text>
                <Typography.Text type="secondary">序号 {event.sequence}</Typography.Text>
              </Space>
              </div>
            ))}
          </div>
        ) : (
          <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="等待任务开始执行" />
        )}
      </Card>
    </section>
  )
}

function eventLabel(type: string, payload: Record<string, unknown>) {
  if (payload.action === 'cancel') return '请求取消任务'
  const labels: Record<string, string> = {
    'run.started': '任务开始执行',
    'run.progress': '任务取得新进展',
    'run.completed': '任务执行完成',
    'run.failed': '任务执行失败',
    'run.cancelled': '任务已取消',
    'approval.required': '任务等待审批',
  }
  return labels[type] ?? type
}
