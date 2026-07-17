import { Alert, Button, Card, Empty, Flex, Form, Input, Space, Spin, Tag, Typography } from 'antd'
import { useState } from 'react'

import { getApiErrorMessage } from '../../auth/api/errors'
import {
  useAddWorkflowVersion,
  usePublishWorkflow,
  useRegisterWorkflow,
  useRollbackWorkflow,
  useWorkflowVersions,
  useWorkflows,
} from '../api/queries'
import type { Workflow } from '../api/workflows'


const SAMPLE_GRAPH = `{
  "entrypoint": "collect",
  "nodes": [
    { "name": "collect", "type": "agent", "config": { "prompt": "整理请求" }, "next": "review" },
    { "name": "review", "type": "human_approval", "config": { "title": "请审批" }, "next": "finish" },
    { "name": "finish", "type": "agent", "config": { "prompt": "输出答复" }, "next": null }
  ]
}`

interface RegisterFormValues {
  name: string
  description: string
  graphText: string
}

interface WorkflowError {
  code?: string
  reason?: string
}

function parseGraph(text: string): { ok: true; value: Record<string, unknown> } | {
  ok: false
  error: string
} {
  try {
    const value = JSON.parse(text) as unknown
    if (typeof value !== 'object' || value === null || Array.isArray(value)) {
      return { ok: false, error: '工作流图必须是 JSON 对象' }
    }
    return { ok: true, value: value as Record<string, unknown> }
  } catch {
    return { ok: false, error: '工作流图 JSON 解析失败' }
  }
}

function workflowGraphErrorMessage(error: unknown, fallback: string): string {
  const detail = (error as { response?: { data?: { detail?: WorkflowError } } })
    ?.response?.data?.detail
  if (detail?.code === 'invalid_workflow_graph') {
    return detail.reason ? `工作流图无效：${detail.reason}` : '工作流图定义无效'
  }
  if (detail?.code === 'workflow_name_exists') {
    return '已存在同名工作流'
  }
  return getApiErrorMessage(error, fallback)
}

function WorkflowVersionsPanel({ workflow }: { workflow: Workflow }) {
  const versions = useWorkflowVersions(workflow.id)
  const publish = usePublishWorkflow(workflow.id)
  const rollback = useRollbackWorkflow(workflow.id)

  if (versions.isPending) {
    return <Flex justify="center"><Spin size="small" /></Flex>
  }

  return (
    <Space orientation="vertical" size={8} style={{ width: '100%' }}>
      {(versions.data ?? []).map((version) => {
        const isPublished = workflow.published_version === version.version
        const isRollback =
          workflow.published_version !== null && version.version < workflow.published_version
        return (
          <Flex key={version.version} align="center" justify="space-between" gap={12}>
            <Space>
              <Typography.Text strong>{`v${version.version}`}</Typography.Text>
              {isPublished && <Tag color="success">当前发布</Tag>}
              <Typography.Text type="secondary">{version.description || '—'}</Typography.Text>
            </Space>
            {!isPublished && (
              <Button
                size="small"
                aria-label={
                  isRollback
                    ? `回滚到 v${version.version}`
                    : `发布 v${version.version}`
                }
                loading={publish.isPending || rollback.isPending}
                onClick={() => {
                  if (isRollback) {
                    rollback.mutate(version.version)
                  } else {
                    publish.mutate(version.version)
                  }
                }}
              >
                {isRollback ? '回滚到此版本' : '发布此版本'}
              </Button>
            )}
          </Flex>
        )
      })}
    </Space>
  )
}

function WorkflowCard({ workflow, canManage }: { workflow: Workflow; canManage: boolean }) {
  const addVersion = useAddWorkflowVersion(workflow.id)
  const [expanded, setExpanded] = useState(false)

  return (
    <Card
      title={workflow.name}
      extra={(
        <Tag color={workflow.status === 'published' ? 'success' : 'default'}>
          {workflow.status === 'published'
            ? `已发布 v${workflow.published_version}`
            : '草稿'}
        </Tag>
      )}
    >
      <Space orientation="vertical" size={12} style={{ width: '100%' }}>
        <Typography.Paragraph ellipsis={{ rows: 2 }}>
          {workflow.description || '（无描述）'}
        </Typography.Paragraph>
        <Typography.Text type="secondary">{`最新版本 v${workflow.latest_version}`}</Typography.Text>
        <Space>
          <Button size="small" onClick={() => setExpanded((value) => !value)}>
            {expanded ? '收起版本' : '查看版本'}
          </Button>
          {canManage && (
            <Button
              size="small"
              loading={addVersion.isPending}
              aria-label={`为 ${workflow.name} 新增版本`}
              onClick={() =>
                addVersion.mutate({ description: '新增版本', graph: JSON.parse(SAMPLE_GRAPH) })}
            >
              新增版本
            </Button>
          )}
        </Space>
        {expanded && <WorkflowVersionsPanel workflow={workflow} />}
      </Space>
    </Card>
  )
}

export function WorkflowsPage({ canManageEmployees }: { canManageEmployees: boolean }) {
  const workflows = useWorkflows()
  const register = useRegisterWorkflow()
  const [form] = Form.useForm<RegisterFormValues>()
  const [graphError, setGraphError] = useState<string | null>(null)

  const submit = (values: RegisterFormValues) => {
    setGraphError(null)
    const graph = parseGraph(values.graphText)
    if (!graph.ok) {
      setGraphError(graph.error)
      return
    }
    register.mutate(
      { name: values.name, description: values.description ?? '', graph: graph.value },
      {
        onSuccess: () => {
          form.resetFields()
        },
      },
    )
  }

  return (
    <section>
      <Flex align="center" justify="space-between" gap={16}>
        <div>
          <Typography.Title level={2}>工作流中心</Typography.Title>
          <Typography.Text type="secondary">
            注册、版本化、发布与回滚固定工作流，供流程/混合数字员工引用
          </Typography.Text>
        </div>
      </Flex>

      {canManageEmployees && (
        <Card title="注册工作流" style={{ marginTop: 16 }}>
          {register.isError && (
            <Alert
              type="error"
              showIcon
              style={{ marginBottom: 12 }}
              title={workflowGraphErrorMessage(register.error, '注册失败，请稍后重试')}
            />
          )}
          {graphError && (
            <Alert type="error" showIcon style={{ marginBottom: 12 }} title={graphError} />
          )}
          <Form<RegisterFormValues>
            form={form}
            layout="vertical"
            requiredMark={false}
            initialValues={{ graphText: SAMPLE_GRAPH }}
            onFinish={submit}
          >
            <Form.Item label="工作流名称" name="name" rules={[{ required: true }]}>
              <Input maxLength={200} placeholder="例如：标准客服流程" />
            </Form.Item>
            <Form.Item label="描述" name="description">
              <Input maxLength={2000} />
            </Form.Item>
            <Form.Item label="工作流图（JSON）" name="graphText" rules={[{ required: true }]}>
              <Input.TextArea rows={10} />
            </Form.Item>
            <Button type="primary" htmlType="submit" loading={register.isPending}>
              注册工作流
            </Button>
          </Form>
        </Card>
      )}

      <div style={{ marginTop: 16 }}>
        {workflows.isPending ? (
          <Flex justify="center"><Spin /></Flex>
        ) : workflows.data?.length ? (
          <Space orientation="vertical" size={16} style={{ width: '100%' }}>
            {workflows.data.map((workflow) => (
              <WorkflowCard
                key={workflow.id}
                workflow={workflow}
                canManage={canManageEmployees}
              />
            ))}
          </Space>
        ) : (
          <Card><Empty description="还没有工作流" /></Card>
        )}
      </div>
    </section>
  )
}
