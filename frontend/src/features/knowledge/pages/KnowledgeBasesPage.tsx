import { Button, Card, Empty, Flex, Form, Input, Modal, Typography } from 'antd'
import { useState } from 'react'
import { useNavigate } from 'react-router-dom'

import { useCreateKnowledgeBase, useKnowledgeBases } from '../api/queries'
import './knowledge.css'


interface CreateValues {
  name: string
  description: string
}

export function KnowledgeBasesPage() {
  const knowledgeBases = useKnowledgeBases()
  const create = useCreateKnowledgeBase()
  const navigate = useNavigate()
  const [open, setOpen] = useState(false)
  const [form] = Form.useForm<CreateValues>()

  const submit = async () => {
    const values = await form.validateFields()
    const value = await create.mutateAsync(values)
    setOpen(false)
    form.resetFields()
    navigate(`/knowledge-bases/${value.id}`)
  }

  return (
    <section>
      <Flex align="center" justify="space-between" gap={16}>
        <div>
          <Typography.Title level={2}>知识库</Typography.Title>
          <Typography.Text type="secondary">管理企业文档、解析状态与检索引用</Typography.Text>
        </div>
        <Button type="primary" onClick={() => setOpen(true)}>创建知识库</Button>
      </Flex>
      {knowledgeBases.data?.length ? (
        <div className="knowledge-grid">
          {knowledgeBases.data.map((item) => (
            <Card key={item.id} hoverable title={item.name} onClick={() => navigate(`/knowledge-bases/${item.id}`)}>
              <Typography.Paragraph type="secondary">
                {item.description || '暂无说明'}
              </Typography.Paragraph>
            </Card>
          ))}
        </div>
      ) : (
        <Card className="knowledge-empty"><Empty description="还没有知识库" /></Card>
      )}
      <Modal
        title="创建知识库"
        open={open}
        okText="创建"
        cancelText="取消"
        confirmLoading={create.isPending}
        onOk={submit}
        onCancel={() => setOpen(false)}
      >
        <Form form={form} layout="vertical" requiredMark={false}>
          <Form.Item label="知识库名称" name="name" rules={[{ required: true }]}>
            <Input placeholder="例如：员工制度" />
          </Form.Item>
          <Form.Item label="说明" name="description" initialValue="">
            <Input.TextArea rows={3} />
          </Form.Item>
        </Form>
      </Modal>
    </section>
  )
}
