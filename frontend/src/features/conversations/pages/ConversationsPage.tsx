import { Card, Empty, Flex, Spin, Typography } from 'antd'
import { useNavigate } from 'react-router-dom'

import { useConversations } from '../api/queries'
import './conversations.css'


export function ConversationsPage() {
  const conversations = useConversations()
  const navigate = useNavigate()

  return (
    <section>
      <Typography.Title level={2}>会话中心</Typography.Title>
      <Typography.Text type="secondary">
        查看多轮会话、消息时间线和跨任务续聊记录
      </Typography.Text>
      {conversations.isPending ? (
        <Flex className="conversation-loading" justify="center"><Spin /></Flex>
      ) : conversations.data?.length ? (
        <div className="conversation-list">
          {conversations.data.map((conversation) => (
            <Card
              key={conversation.id}
              hoverable
              className="conversation-card"
              onClick={() => navigate(`/conversations/${conversation.id}`)}
            >
              <Typography.Text strong>{conversation.title}</Typography.Text>
              <Typography.Paragraph type="secondary">
                线程 {conversation.thread_id}
              </Typography.Paragraph>
            </Card>
          ))}
        </div>
      ) : (
        <Card className="conversation-empty"><Empty description="还没有会话" /></Card>
      )}
    </section>
  )
}
