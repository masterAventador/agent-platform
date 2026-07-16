import { useQueryClient } from '@tanstack/react-query'
import { Button, Card, Descriptions, Empty, Flex, List, Modal, Space, Spin, Tag, Typography } from 'antd'
import { useEffect, useState } from 'react'
import { useParams } from 'react-router-dom'

import { useActiveWorkspaceId } from '../../workspaces/store'
import { ResourceAccessError } from '../../system/components/ResourceAccessError'
import { getPlatformAdapter } from '../../../platform'
import {
  runKeys,
  useArtifacts,
  useControlRun,
  useDeleteArtifact,
  useRun,
  useRunEvents,
} from '../api/queries'
import { downloadArtifact, type Artifact } from '../api/runs'
import { StructuredOutput } from '../../dynamic-io/StructuredOutput'
import { formatRunEvent, formatRunInput, knowledgeCitations, runStatusLabels } from './status'
import './runs.css'


const terminalStatuses = new Set(['completed', 'failed', 'cancelled'])
const streamEvents = [
  'run.started', 'run.progress', 'message.output', 'approval.required',
  'knowledge.retrieved', 'artifact.created', 'run.completed', 'run.failed', 'run.cancelled',
]

export function RunDetailPage({
  canExecuteRuns,
  canManageRuns,
}: {
  canExecuteRuns: boolean
  canManageRuns: boolean
}) {
  const { runId } = useParams()
  const run = useRun(runId)
  const events = useRunEvents(runId)
  const artifacts = useArtifacts(runId)
  const deleteArtifact = useDeleteArtifact(runId ?? '')
  const control = useControlRun(runId ?? '')
  const tenantId = useActiveWorkspaceId()
  const queryClient = useQueryClient()
  const runStatus = run.data?.status
  const [preview, setPreview] = useState<{ artifact: Artifact; content: string } | null>(null)

  useEffect(() => {
    if (!runId || !tenantId || !runStatus || terminalStatuses.has(runStatus)) return
    const search = new URLSearchParams({ tenant_id: tenantId })
    const source = new EventSource(`/api/v1/runs/${runId}/stream?${search}`, {
      withCredentials: true,
    })
    const refresh = () => {
      void queryClient.invalidateQueries({ queryKey: runKeys.detail(tenantId, runId) })
      void queryClient.invalidateQueries({ queryKey: runKeys.events(tenantId, runId) })
      void queryClient.invalidateQueries({ queryKey: runKeys.artifacts(tenantId, runId) })
    }
    streamEvents.forEach((type) => source.addEventListener(type, refresh))
    return () => source.close()
  }, [queryClient, runId, runStatus, tenantId])

  if (run.isPending) {
    return <Flex className="run-loading" justify="center"><Spin /></Flex>
  }
  if (run.isError || !run.data) {
    return <ResourceAccessError error={run.error} resourceName="任务" />
  }
  const data = run.data
  const status = runStatusLabels[data.status]
  const cancellable = !terminalStatuses.has(data.status)
  const cancelRequested = cancellable && (
    (control.variables?.action === 'cancel' && (control.isPending || control.isSuccess))
    || (events.data ?? []).some((event) => event.payload.action === 'cancel_requested')
  )
  const approvalId = [...(events.data ?? [])]
    .reverse()
    .find((event) => event.type === 'approval.required')?.payload.approval_id
  const structuredOutput = [...(events.data ?? [])]
    .reverse()
    .find((event) => event.payload.output !== undefined)
    ?.payload.output

  return (
    <section className="run-detail">
      <Flex align="center" justify="space-between" gap={16}>
        <Space align="center">
          <Typography.Title level={2}>任务详情</Typography.Title>
          <Tag color={status.color}>{status.text}</Tag>
        </Space>
        <Space>
          {canExecuteRuns && data.status === 'waiting_for_input' && (
            <Button
              type="primary"
              loading={control.isPending}
              onClick={() => canExecuteRuns && control.mutate({ action: 'resume' })}
            >
              继续执行
            </Button>
          )}
          {canManageRuns
            && data.status === 'waiting_for_approval'
            && typeof approvalId === 'string' && (
            <>
              <Button
                type="primary"
                loading={control.isPending}
                onClick={() => canManageRuns && control.mutate({ action: 'approve', approvalId })}
              >
                批准
              </Button>
              <Button
                danger
                loading={control.isPending}
                onClick={() => canManageRuns && control.mutate({ action: 'reject', approvalId })}
              >
                拒绝
              </Button>
            </>
          )}
          {canExecuteRuns && cancellable && (
            <Button
              danger
              loading={control.isPending}
              disabled={cancelRequested}
              onClick={() => canExecuteRuns && control.mutate({ action: 'cancel' })}
            >
              {cancelRequested ? '取消处理中' : '取消任务'}
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
            {events.data.map((event) => {
              const presentation = formatRunEvent(event.type, event.payload)
              const citations = event.type === 'knowledge.retrieved'
                ? knowledgeCitations(event.payload)
                : []
              return (
                <div
                  className="run-event-item"
                  data-artifact-id={event.payload.artifact_id}
                  key={event.event_id}
                >
                  <Space orientation="vertical" size={2}>
                    <Typography.Text strong>{presentation.label}</Typography.Text>
                    {presentation.content && (
                      <Typography.Text>{presentation.content}</Typography.Text>
                    )}
                    {citations.length > 0 && (
                      <div className="run-knowledge-citations">
                        {citations.map((citation) => (
                          <div className="run-knowledge-citation" key={citation.chunkId}>
                            <Typography.Text strong>{citation.documentName}</Typography.Text>
                            <Typography.Paragraph type="secondary">
                              {citation.content}
                            </Typography.Paragraph>
                          </div>
                        ))}
                      </div>
                    )}
                    <Typography.Text type="secondary">序号 {event.sequence}</Typography.Text>
                  </Space>
                </div>
              )
            })}
          </div>
        ) : (
          <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="等待任务开始执行" />
        )}
      </Card>
      <StructuredOutput
        runId={data.id}
        outputSchema={data.output_schema}
        output={structuredOutput}
      />
      <Card className="run-artifacts" title="任务产物">
        {artifacts.data?.length ? (
          <List
            dataSource={artifacts.data}
            renderItem={(artifact) => (
              <List.Item
                actions={[
                  <Button
                    aria-label={`预览 ${artifact.name}`}
                    key="preview"
                    onClick={async () => {
                      const bytes = await downloadArtifact(data.tenant_id, artifact.id)
                      const content = artifact.media_type.startsWith('text/')
                        || artifact.media_type === 'application/json'
                        ? new TextDecoder().decode(bytes)
                        : `该文件类型不支持内嵌预览，可下载后查看（${artifact.media_type}）`
                      setPreview({ artifact, content })
                    }}
                  >
                    预览
                  </Button>,
                  <Button
                    aria-label={`下载 ${artifact.name}`}
                    key="download"
                    onClick={async () => {
                      const bytes = await downloadArtifact(data.tenant_id, artifact.id)
                      await getPlatformAdapter().saveFile({
                        suggestedName: artifact.name,
                        bytes,
                      })
                    }}
                  >
                    下载
                  </Button>,
                  <Button
                    aria-label={`定位 ${artifact.name}`}
                    key="locate"
                    onClick={() => document
                      .querySelector(`[data-artifact-id="${artifact.id}"]`)
                      ?.scrollIntoView({ behavior: 'smooth', block: 'center' })}
                  >
                    定位
                  </Button>,
                  canExecuteRuns ? (
                    <Button
                      aria-label={`删除 ${artifact.name}`}
                      danger
                      key="delete"
                      loading={deleteArtifact.isPending}
                      onClick={() => deleteArtifact.mutate(artifact.id)}
                    >
                      删除
                    </Button>
                  ) : null,
                ].filter(Boolean)}
              >
                <List.Item.Meta
                  title={artifact.name}
                  description={`${artifact.media_type} · ${artifact.size_bytes} 字节`}
                />
              </List.Item>
            )}
          />
        ) : (
          <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="暂无任务产物" />
        )}
      </Card>
      <Modal
        title={preview?.artifact.name}
        open={preview !== null}
        footer={<Button onClick={() => setPreview(null)}>关闭</Button>}
        onCancel={() => setPreview(null)}
      >
        <Typography.Paragraph className="run-artifact-preview">
          {preview?.content}
        </Typography.Paragraph>
      </Modal>
    </section>
  )
}
