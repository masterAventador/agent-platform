import { Alert, Button, Card, Empty, Flex, Input, Modal, Space, Tag, Typography } from 'antd'
import { useMemo, useState } from 'react'
import { useNavigate, useParams } from 'react-router-dom'

import { getApiErrorMessage } from '../../auth/api/errors'
import {
  useDeleteKnowledgeDocument,
  useDeleteKnowledgeBase,
  useKnowledgeBases,
  useKnowledgeDocuments,
  useKnowledgeSearch,
  useReplaceKnowledgeDocument,
  useRetryKnowledgeDocument,
  useUploadKnowledgeDocuments,
} from '../api/queries'
import './knowledge.css'


const documentStatuses: Record<string, { color: string; text: string }> = {
  UNSTART: { color: 'default', text: '等待解析' },
  RUNNING: { color: 'processing', text: '解析中' },
  DONE: { color: 'success', text: '解析完成' },
  FAIL: { color: 'error', text: '解析失败' },
  CANCEL: { color: 'default', text: '已取消' },
}

export function KnowledgeBaseDetailPage({ canManageKnowledge }: { canManageKnowledge: boolean }) {
  const { knowledgeBaseId = '' } = useParams()
  const knowledgeBases = useKnowledgeBases()
  const documents = useKnowledgeDocuments(knowledgeBaseId)
  const upload = useUploadKnowledgeDocuments(knowledgeBaseId)
  const retryDocument = useRetryKnowledgeDocument(knowledgeBaseId)
  const replaceDocument = useReplaceKnowledgeDocument(knowledgeBaseId)
  const deleteDocument = useDeleteKnowledgeDocument(knowledgeBaseId)
  const remove = useDeleteKnowledgeBase()
  const search = useKnowledgeSearch(knowledgeBaseId)
  const navigate = useNavigate()
  const [files, setFiles] = useState<File[]>([])
  const [question, setQuestion] = useState('')
  const [deleteOpen, setDeleteOpen] = useState(false)
  const [deleteError, setDeleteError] = useState<unknown>()
  const value = useMemo(
    () => knowledgeBases.data?.find((item) => item.id === knowledgeBaseId),
    [knowledgeBaseId, knowledgeBases.data],
  )

  return (
    <section className="knowledge-detail">
      <Flex align="center" justify="space-between" gap={16}>
        <Typography.Title level={2}>{value?.name ?? '知识库详情'}</Typography.Title>
        {canManageKnowledge && (
          <Button
            danger
            onClick={() => {
              setDeleteError(undefined)
              setDeleteOpen(true)
            }}
          >
            删除知识库
          </Button>
        )}
      </Flex>
      <Typography.Text type="secondary">{value?.description}</Typography.Text>
      <Card className="knowledge-section" title="文档">
        {canManageKnowledge && (
          <Flex gap={12} align="center">
            <input
              aria-label="选择文档"
              multiple
              type="file"
              onChange={(event) => setFiles(Array.from(event.target.files ?? []))}
            />
            <Button
              type="primary"
              disabled={files.length === 0}
              loading={upload.isPending}
              onClick={() => canManageKnowledge
                && files.length > 0
                && upload.mutate(files, { onSuccess: () => setFiles([]) })}
            >
              批量上传并解析
            </Button>
          </Flex>
        )}
        <div className="knowledge-documents">
          {documents.data?.length ? documents.data.map((document) => {
            const status = documentStatuses[document.status] ?? {
              color: 'default', text: document.status,
            }
            return (
              <Flex
                key={document.provider_id}
                className="knowledge-document"
                gap={12}
                justify="space-between"
                wrap="wrap"
              >
                <Space>
                  <Typography.Text>{document.name}</Typography.Text>
                  <Tag color={status.color}>{status.text}</Tag>
                  <Typography.Text type="secondary">
                    {document.size_bytes} 字节 · {document.chunk_count} 片段
                  </Typography.Text>
                </Space>
                {canManageKnowledge && (
                  <Space wrap>
                    <Button
                      size="small"
                      loading={retryDocument.isPending}
                      onClick={() => retryDocument.mutate(document.provider_id)}
                    >
                      {`重试解析 ${document.name}`}
                    </Button>
                    <input
                      aria-label={`选择替换文档 ${document.name}`}
                      type="file"
                      onChange={(event) => {
                        const replacement = event.target.files?.[0]
                        if (replacement) {
                          replaceDocument.mutate({
                            documentId: document.provider_id,
                            file: replacement,
                          })
                        }
                        event.currentTarget.value = ''
                      }}
                    />
                    <Button
                      danger
                      size="small"
                      loading={deleteDocument.isPending}
                      onClick={() => deleteDocument.mutate(document.provider_id)}
                    >
                      {`删除文档 ${document.name}`}
                    </Button>
                  </Space>
                )}
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
      <Modal
        title="确认删除知识库"
        open={deleteOpen}
        okText="确认删除"
        okButtonProps={{ danger: true }}
        cancelText="取消"
        confirmLoading={remove.isPending}
        onCancel={() => {
          setDeleteOpen(false)
          setDeleteError(undefined)
        }}
        onOk={async () => {
          if (!canManageKnowledge) return
          try {
            await remove.mutateAsync(knowledgeBaseId)
            navigate('/knowledge-bases', { replace: true })
          } catch (error) {
            setDeleteError(error)
          }
        }}
      >
        {deleteError !== undefined && (
          <Alert
            type="error"
            showIcon
            title={getApiErrorMessage(deleteError, '知识库删除失败，请稍后重试')}
          />
        )}
        删除后知识库及其文档将无法恢复。
      </Modal>
    </section>
  )
}
