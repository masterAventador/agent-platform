import { Button, Card, Empty, Flex, Input, List, Space, Spin, Tag, Typography } from 'antd'
import { useState } from 'react'
import { Link, useParams } from 'react-router-dom'

import { ResourceAccessError } from '../../system/components/ResourceAccessError'
import {
  useAppendConversationMessage,
  useCancelConversationRun,
  useConversation,
  useRetryConversation,
} from '../api/queries'
import './conversations.css'


const terminalRunStatuses = new Set(['completed', 'failed', 'cancelled'])

const runStatusText: Record<string, string> = {
  queued: '排队中',
  running: '执行中',
  waiting_for_input: '等待输入',
  waiting_for_approval: '等待审批',
  completed: '已完成',
  failed: '失败',
  cancelled: '已取消',
}

const roleText: Record<string, string> = {
  user: '用户',
  assistant: '数字员工',
  system: '系统',
  error: '错误',
}

export function ConversationDetailPage({
  currentUserId,
  canManageRuns,
}: {
  currentUserId: string
  canManageRuns: boolean
}) {
  const { conversationId } = useParams()
  const conversation = useConversation(conversationId)
  const appendMessage = useAppendConversationMessage(conversationId ?? '')
  const retry = useRetryConversation(conversationId ?? '')
  const cancelRun = useCancelConversationRun(conversationId ?? '')
  const [content, setContent] = useState('')

  if (conversation.isPending) {
    return <Flex className="conversation-loading" justify="center"><Spin /></Flex>
  }
  if (conversation.isError || !conversation.data) {
    return <ResourceAccessError error={conversation.error} resourceName="会话" />
  }

  const submit = () => {
    const value = content.trim()
    if (!value) return
    appendMessage.mutate({ content: value, attachmentIds: [], dispatch: true })
    setContent('')
  }

  return (
    <section className="conversation-detail">
      <Flex align="center" justify="space-between" gap={16}>
        <Space direction="vertical" size={0}>
          <Typography.Title level={2}>{conversation.data.title}</Typography.Title>
          <Typography.Text type="secondary">
            线程 {conversation.data.thread_id}
          </Typography.Text>
        </Space>
      </Flex>

      <Card title="消息时间线">
        {conversation.data.messages.length ? (
          <List
            className="conversation-message-list"
            dataSource={conversation.data.messages}
            renderItem={(message) => (
              <List.Item>
                <Space direction="vertical" size={4}>
                  <Space>
                    <Tag>{roleText[message.role] ?? message.role}</Tag>
                    <Typography.Text type="secondary">#{message.sequence}</Typography.Text>
                  </Space>
                  <Typography.Paragraph>{message.content}</Typography.Paragraph>
                  {message.attachment_ids.map((attachmentId) => (
                    <Typography.Text key={attachmentId} type="secondary">
                      附件 {attachmentId}
                    </Typography.Text>
                  ))}
                </Space>
              </List.Item>
            )}
          />
        ) : (
          <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="暂无消息" />
        )}
      </Card>

      <Card title="关联任务">
        {conversation.data.runs.length ? (
          <List
            dataSource={conversation.data.runs}
            renderItem={(run) => (
              <List.Item
                actions={[
                  !terminalRunStatuses.has(run.status)
                  && (canManageRuns || run.created_by === currentUserId) ? (
                    <Button
                      key="cancel"
                      danger
                      loading={cancelRun.isPending && cancelRun.variables === run.id}
                      onClick={() => cancelRun.mutate(run.id)}
                    >
                      取消任务
                    </Button>
                  ) : null,
                  run.status === 'failed' ? (
                    <Button
                      key="retry"
                      loading={retry.isPending}
                      onClick={() => retry.mutate(run.id)}
                    >
                      重试失败任务
                    </Button>
                  ) : null,
                  <Link key="detail" to={`/runs/${run.id}`}>任务详情</Link>,
                ].filter(Boolean)}
              >
                <List.Item.Meta
                  title={`任务 ${run.id.slice(0, 8)}`}
                  description={run.status === 'failed'
                    ? `失败：${run.error_code ?? run.error_message ?? 'unknown'}`
                    : runStatusText[run.status]}
                />
              </List.Item>
            )}
          />
        ) : (
          <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="暂无关联任务" />
        )}
      </Card>

      <Card title="追加输入">
        <Space.Compact className="conversation-input">
          <Input.TextArea
            aria-label="追加消息"
            autoSize={{ minRows: 2, maxRows: 6 }}
            value={content}
            onChange={(event) => setContent(event.target.value)}
            onPressEnter={(event) => {
              if (!event.shiftKey) {
                event.preventDefault()
                submit()
              }
            }}
          />
          <Button
            aria-label="发送"
            type="primary"
            loading={appendMessage.isPending}
            onClick={submit}
          >
            发送
          </Button>
        </Space.Compact>
      </Card>
    </section>
  )
}
