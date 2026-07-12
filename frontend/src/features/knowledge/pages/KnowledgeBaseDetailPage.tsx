import { Button, Card, Empty, Flex, Input, Space, Tag, Typography } from 'antd'
import { useMemo, useState } from 'react'
import { useParams } from 'react-router-dom'

import { useKnowledgeBases, useKnowledgeDocuments, useKnowledgeSearch, useUploadKnowledgeDocument } from '../api/queries'
import './knowledge.css'


const documentStatuses: Record<string, { color: string; text: string }> = {
  UNSTART: { color: 'default', text: '等待解析' },
  RUNNING: { color: 'processing', text: '解析中' },
  DONE: { color: 'success', text: '解析完成' },
  FAIL: { color: 'error', text: '解析失败' },
  CANCEL: { color: 'default', text: '已取消' },
}

export function KnowledgeBaseDetailPage() {
  const { knowledgeBaseId = '' } = useParams()
  const knowledgeBases = useKnowledgeBases()
  const documents = useKnowledgeDocuments(knowledgeBaseId)
  const upload = useUploadKnowledgeDocument(knowledgeBaseId)
  const search = useKnowledgeSearch(knowledgeBaseId)
  const [file, setFile] = useState<File>()
  const [question, setQuestion] = useState('')
  const value = useMemo(
    () => knowledgeBases.data?.find((item) => item.id === knowledgeBaseId),
    [knowledgeBaseId, knowledgeBases.data],
  )

  return (
    <section className="knowledge-detail">
      <Typography.Title level={2}>{value?.name ?? '知识库详情'}</Typography.Title>
      <Typography.Text type="secondary">{value?.description}</Typography.Text>
      <Card className="knowledge-section" title="文档">
        <Flex gap={12} align="center">
          <input
            aria-label="选择文档"
            type="file"
            onChange={(event) => setFile(event.target.files?.[0])}
          />
          <Button
            type="primary"
            disabled={!file}
            loading={upload.isPending}
            onClick={() => file && upload.mutate(file)}
          >
            上传并解析
          </Button>
        </Flex>
        <div className="knowledge-documents">
          {documents.data?.length ? documents.data.map((document) => {
            const status = documentStatuses[document.status] ?? {
              color: 'default', text: document.status,
            }
            return (
              <Flex key={document.provider_id} className="knowledge-document" justify="space-between">
                <Typography.Text>{document.name}</Typography.Text>
                <Tag color={status.color}>{status.text}</Tag>
              </Flex>
            )
          }) : <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="还没有文档" />}
        </div>
      </Card>
      <Card className="knowledge-section" title="检索测试">
        <Space.Compact block>
          <Input
            aria-label="检索问题"
            value={question}
            placeholder="输入问题，验证召回内容和引用"
            onChange={(event) => setQuestion(event.target.value)}
          />
          <Button
            type="primary"
            disabled={!question.trim()}
            loading={search.isPending}
            onClick={() => search.mutate(question.trim())}
          >
            检索
          </Button>
        </Space.Compact>
        <div className="knowledge-results">
          {search.data?.citations.map((citation) => (
            <Card key={citation.chunk_id} size="small">
              <Typography.Paragraph>{citation.content}</Typography.Paragraph>
              <Typography.Text type="secondary">
                {citation.document_name} · 相似度 {citation.score.toFixed(3)}
              </Typography.Text>
            </Card>
          ))}
        </div>
      </Card>
    </section>
  )
}
