import {
  Alert,
  Button,
  Flex,
  Form,
  Input,
  Modal,
  Popconfirm,
  Select,
  Space,
  Table,
  Tag,
  Typography,
} from 'antd'
import { useState } from 'react'

import {
  useCreateMemory,
  useDeleteMemory,
  useMemories,
  useUpdateMemory,
} from '../api/queries'
import type { Memory, MemoryScope } from '../api/memories'


const scopeLabels: Record<MemoryScope, string> = {
  tenant: '企业',
  user: '用户',
  employee: '员工',
  conversation: '会话',
}

const sourceLabels: Record<Memory['source'], string> = {
  run: '任务提取',
  conversation: '会话提取',
  manual: '手工录入',
}

const scopeFilterOptions = [
  { value: 'all', label: '全部命名空间' },
  { value: 'tenant', label: '企业级' },
  { value: 'user', label: '用户级' },
  { value: 'employee', label: '员工级' },
  { value: 'conversation', label: '会话级' },
]

interface MemoryFormValues {
  scope: MemoryScope
  content: string
}

export function MemoriesPage() {
  const [scopeFilter, setScopeFilter] = useState<'all' | MemoryScope>('all')
  const [keyword, setKeyword] = useState('')
  const [editing, setEditing] = useState<Memory | null>(null)
  const [creating, setCreating] = useState(false)
  const [editForm] = Form.useForm<{ content: string }>()
  const [createForm] = Form.useForm<MemoryFormValues>()

  const memories = useMemories({
    scope: scopeFilter === 'all' ? undefined : scopeFilter,
    q: keyword || undefined,
  })
  const createMemory = useCreateMemory()
  const updateMemory = useUpdateMemory()
  const deleteMemory = useDeleteMemory()

  const submitCorrection = async (values: { content: string }) => {
    if (!editing) return
    try {
      await updateMemory.mutateAsync({
        memoryId: editing.id,
        input: { content: values.content.trim() },
      })
      setEditing(null)
    } catch {
      // 错误由 Mutation 状态统一渲染。
    }
  }

  const toggleStatus = async (memory: Memory) => {
    try {
      await updateMemory.mutateAsync({
        memoryId: memory.id,
        input: { status: memory.status === 'active' ? 'disabled' : 'active' },
      })
    } catch {
      // 错误由 Mutation 状态统一渲染。
    }
  }

  const removeMemory = async (memoryId: string) => {
    try {
      await deleteMemory.mutateAsync(memoryId)
    } catch {
      // 错误由 Mutation 状态统一渲染。
    }
  }

  const submitCreation = async (values: MemoryFormValues) => {
    try {
      await createMemory.mutateAsync({
        scope: values.scope,
        content: values.content.trim(),
      })
      setCreating(false)
      createForm.resetFields()
    } catch {
      // 错误由 Mutation 状态统一渲染。
    }
  }

  return (
    <section className="memories-page">
      <Flex align="center" justify="space-between" gap={16} wrap>
        <Space orientation="vertical" size={4}>
          <Typography.Title level={2}>记忆中心</Typography.Title>
          <Typography.Text type="secondary">
            查看、纠正、禁用或删除数字员工的长期记忆；记忆按企业、用户、员工、会话四级命名空间隔离
          </Typography.Text>
        </Space>
        <Button type="primary" onClick={() => setCreating(true)}>新增记忆</Button>
      </Flex>

      <Flex gap={12} wrap style={{ marginTop: 16, marginBottom: 16 }}>
        <Select
          aria-label="命名空间筛选"
          style={{ width: 180 }}
          virtual={false}
          value={scopeFilter}
          options={scopeFilterOptions}
          onChange={(value) => setScopeFilter(value as 'all' | MemoryScope)}
        />
        <Input.Search
          aria-label="搜索记忆"
          style={{ maxWidth: 320 }}
          placeholder="按内容关键词搜索"
          allowClear
          onSearch={(value) => setKeyword(value.trim())}
        />
      </Flex>

      {memories.isError && (
        <Alert type="error" showIcon title="记忆列表加载失败，请稍后重试" />
      )}
      {(createMemory.isError || updateMemory.isError || deleteMemory.isError) && (
        <Alert type="error" showIcon title="记忆操作失败，请检查内容或权限后重试" />
      )}

      <Table<Memory>
        rowKey="id"
        loading={memories.isPending}
        dataSource={memories.data ?? []}
        locale={{ emptyText: '暂无长期记忆' }}
        pagination={false}
        columns={[
          {
            title: '命名空间',
            dataIndex: 'scope',
            width: 110,
            render: (scope: MemoryScope) => <Tag>{scopeLabels[scope]}</Tag>,
          },
          {
            title: '记忆内容',
            dataIndex: 'content',
            render: (content: string) => (
              <Typography.Paragraph style={{ marginBottom: 0 }} ellipsis={{ rows: 3 }}>
                {content}
              </Typography.Paragraph>
            ),
          },
          {
            title: '来源',
            dataIndex: 'source',
            width: 110,
            render: (source: Memory['source']) => sourceLabels[source],
          },
          {
            title: '状态',
            width: 110,
            render: (_, memory) => {
              if (memory.expired) return <Tag color="orange">已过期</Tag>
              if (memory.status === 'disabled') return <Tag color="red">已禁用</Tag>
              return <Tag color="green">生效中</Tag>
            },
          },
          {
            title: '更新时间',
            dataIndex: 'updated_at',
            width: 180,
            render: (value: string) => new Date(value).toLocaleString('zh-CN'),
          },
          {
            title: '操作',
            width: 240,
            render: (_, memory) => (
              <Space>
                <Button
                  size="small"
                  onClick={() => {
                    setEditing(memory)
                    editForm.setFieldsValue({ content: memory.content })
                  }}
                >
                  纠正
                </Button>
                <Button size="small" onClick={() => void toggleStatus(memory)}>
                  {memory.status === 'active' ? '禁用' : '启用'}
                </Button>
                <Popconfirm
                  title="删除后不可恢复，任务将无法再召回该记忆"
                  okText="确认删除"
                  cancelText="取消"
                  onConfirm={() => void removeMemory(memory.id)}
                >
                  <Button size="small" danger>删除</Button>
                </Popconfirm>
              </Space>
            ),
          },
        ]}
      />

      <Modal
        title="纠正记忆"
        open={editing !== null}
        onCancel={() => setEditing(null)}
        footer={null}
        destroyOnHidden
      >
        <Form form={editForm} layout="vertical" onFinish={submitCorrection}>
          <Form.Item
            label="记忆内容"
            name="content"
            rules={[{ required: true, message: '请输入记忆内容' }, { max: 4000 }]}
          >
            <Input.TextArea rows={4} />
          </Form.Item>
          <Flex justify="flex-end" gap={8}>
            <Button onClick={() => setEditing(null)}>取消</Button>
            <Button type="primary" htmlType="submit" loading={updateMemory.isPending}>
              保存
            </Button>
          </Flex>
        </Form>
      </Modal>

      <Modal
        title="新增记忆"
        open={creating}
        onCancel={() => setCreating(false)}
        footer={null}
        destroyOnHidden
      >
        <Form
          form={createForm}
          layout="vertical"
          initialValues={{ scope: 'user' }}
          onFinish={submitCreation}
        >
          <Form.Item label="命名空间" name="scope" rules={[{ required: true }]}>
            <Select
              virtual={false}
              options={[
                { value: 'user', label: '用户级（仅本人任务可召回）' },
                { value: 'tenant', label: '企业级（需要管理权限）' },
              ]}
            />
          </Form.Item>
          <Form.Item
            label="记忆内容"
            name="content"
            rules={[{ required: true, message: '请输入记忆内容' }, { max: 4000 }]}
          >
            <Input.TextArea rows={4} placeholder="例如：客户偏好在工作日上午沟通" />
          </Form.Item>
          <Flex justify="flex-end" gap={8}>
            <Button onClick={() => setCreating(false)}>取消</Button>
            <Button type="primary" htmlType="submit" loading={createMemory.isPending}>
              保存
            </Button>
          </Flex>
        </Form>
      </Modal>
    </section>
  )
}
