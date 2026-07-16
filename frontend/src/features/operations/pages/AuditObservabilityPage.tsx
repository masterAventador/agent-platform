import { Alert, Button, Card, Empty, Flex, Space, Spin, Table, Tag, Typography } from 'antd'

import { getApiErrorMessage } from '../../auth/api/errors'
import type { AuditEvent } from '../api/audit'
import { useAuditEvents } from '../api/queries'
import './audit-observability.css'


type AuditMetadata = Record<string, unknown>

const JAEGER_LOCAL_URL = 'http://127.0.0.1:16686/'

const formatDateTime = (value: string) => new Date(value).toLocaleString('zh-CN', {
  year: 'numeric',
  month: '2-digit',
  day: '2-digit',
  hour: '2-digit',
  minute: '2-digit',
  second: '2-digit',
  hour12: false,
})

const metadataSummary = (metadata: AuditMetadata) => {
  const keys = Object.keys(metadata)
  if (keys.length === 0) return '无扩展字段'
  return `${keys.length} 个扩展字段：${keys.slice(0, 4).join('、')}`
}

const outcomeColor = (outcome: string) => {
  if (outcome === 'succeeded') return 'success'
  if (outcome === 'failed') return 'error'
  if (outcome === 'denied') return 'warning'
  return 'default'
}

export function AuditObservabilityPage() {
  const auditEvents = useAuditEvents()

  const columns = [
    {
      title: '发生时间',
      dataIndex: 'occurred_at',
      key: 'occurred_at',
      render: (value: string) => <time dateTime={value}>{formatDateTime(value)}</time>,
    },
    {
      title: '动作',
      dataIndex: 'action',
      key: 'action',
      render: (value: string) => <Typography.Text code>{value}</Typography.Text>,
    },
    {
      title: '资源',
      key: 'resource',
      render: (_: unknown, record: AuditEvent) => (
        <Space orientation="vertical" size={0}>
          <Typography.Text>{record.resource_type}</Typography.Text>
          {record.resource_id !== null && (
            <Typography.Text type="secondary" code>{record.resource_id.slice(0, 8)}</Typography.Text>
          )}
        </Space>
      ),
    },
    {
      title: '结果',
      dataIndex: 'outcome',
      key: 'outcome',
      render: (value: string) => <Tag color={outcomeColor(value)}>{value}</Tag>,
    },
    {
      title: '关联 ID',
      dataIndex: 'correlation_id',
      key: 'correlation_id',
      render: (value: string | null) => value === null ? (
        <Typography.Text type="secondary">无</Typography.Text>
      ) : (
        <Typography.Text code>{value}</Typography.Text>
      ),
    },
    {
      title: '安全摘要',
      dataIndex: 'metadata',
      key: 'metadata',
      render: (value: AuditMetadata) => (
        <Typography.Text type="secondary">{metadataSummary(value)}</Typography.Text>
      ),
    },
  ]

  return (
    <section>
      <Typography.Title level={2}>审计与观测</Typography.Title>
      <Typography.Paragraph type="secondary">
        统一查看平台审计事件，并通过 correlation_id 关联本机链路追踪、Metrics 和 Logs。
      </Typography.Paragraph>

      <Card className="audit-observability-card">
        <Space orientation="vertical" size="small">
          <Alert
            type="info"
            showIcon
            title="本机观测栈已接收 OTLP Trace、Metrics 和 Logs"
            description="Trace 转发到 Jaeger；Metrics 和 Logs 在当前开发阶段通过 Collector debug exporter 输出，保持无持久化状态。"
          />
          <Space wrap>
            <a href={JAEGER_LOCAL_URL} target="_blank" rel="noreferrer">
              打开 Jaeger 本机链路追踪
            </a>
            <Typography.Text type="secondary">
              JSONL 导出接口：<Typography.Text code>/api/v1/audit/events/export?format=jsonl</Typography.Text>
            </Typography.Text>
          </Space>
        </Space>
      </Card>

      {auditEvents.isPending ? (
        <Flex className="audit-observability-loading" justify="center">
          <Spin aria-label="正在加载审计事件" />
        </Flex>
      ) : auditEvents.isError ? (
        <Alert
          className="audit-observability-alert"
          type="error"
          showIcon
          title={getApiErrorMessage(auditEvents.error, '审计事件加载失败')}
          action={(
            <Button size="small" onClick={() => void auditEvents.refetch()}>
              重新加载
            </Button>
          )}
        />
      ) : auditEvents.data?.length ? (
        <Card className="audit-observability-card">
          <Table<AuditEvent>
            rowKey="id"
            pagination={false}
            scroll={{ x: 1080 }}
            dataSource={auditEvents.data}
            columns={columns}
          />
        </Card>
      ) : (
        <Card className="audit-observability-card">
          <Empty description="当前没有审计事件" />
        </Card>
      )}
    </section>
  )
}
