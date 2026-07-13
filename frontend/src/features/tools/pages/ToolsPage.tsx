import {
  Alert,
  Button,
  Card,
  Empty,
  Flex,
  Form,
  Input,
  Modal,
  Select,
  Space,
  Table,
  Tag,
  Typography,
} from 'antd'
import { useState } from 'react'

import { getApiErrorMessage } from '../../auth/api/errors'
import {
  useCreateMcpServer,
  useCreateTool,
  useMcpServers,
  useSetMcpServerEnabled,
  useSetToolEnabled,
  useTools,
} from '../api/queries'
import type { McpServer, McpTransport, Tool, ToolRiskLevel } from '../api/tools'
import './tools.css'


interface ServerFormValues {
  name: string
  transport: McpTransport
  endpoint?: string
  command?: string
  args?: string[]
  secretReference?: string
}

interface ToolFormValues {
  serverId: string
  name: string
  description: string
  inputSchema: string
  riskLevel: ToolRiskLevel
}

const riskLabels: Record<ToolRiskLevel, string> = {
  read: '只读',
  write: '写入',
  external: '外部操作',
  destructive: '破坏性操作',
}

const riskColors: Record<ToolRiskLevel, string> = {
  read: 'blue',
  write: 'gold',
  external: 'orange',
  destructive: 'red',
}

export function ToolsPage({ canManageWorkspace }: { canManageWorkspace: boolean }) {
  const servers = useMcpServers()
  const tools = useTools()
  const createServer = useCreateMcpServer()
  const createTool = useCreateTool()
  const setServerEnabled = useSetMcpServerEnabled()
  const setToolEnabled = useSetToolEnabled()
  const [serverOpen, setServerOpen] = useState(false)
  const [toolOpen, setToolOpen] = useState(false)
  const [serverForm] = Form.useForm<ServerFormValues>()
  const [toolForm] = Form.useForm<ToolFormValues>()
  const transport = Form.useWatch('transport', serverForm) ?? 'streamable_http'
  const serverById = new Map(servers.data?.map((server) => [server.id, server]))

  const closeServer = () => {
    setServerOpen(false)
    serverForm.resetFields()
    createServer.reset()
  }

  const closeTool = () => {
    setToolOpen(false)
    toolForm.resetFields()
    createTool.reset()
  }

  const submitServer = async (values: ServerFormValues) => {
    try {
      await createServer.mutateAsync({
        name: values.name,
        transport: values.transport,
        endpoint: values.transport === 'streamable_http' ? values.endpoint : undefined,
        command: values.transport === 'stdio' ? values.command : undefined,
        args: values.transport === 'stdio' ? (values.args ?? []) : [],
        secret_reference: values.secretReference || undefined,
        enabled: true,
      })
      closeServer()
    } catch {
      // Mutation 错误在弹窗内统一展示。
    }
  }

  const submitTool = async (values: ToolFormValues) => {
    try {
      await createTool.mutateAsync({
        server_id: values.serverId,
        name: values.name,
        description: values.description,
        input_schema: JSON.parse(values.inputSchema) as Record<string, unknown>,
        risk_level: values.riskLevel,
        enabled: true,
      })
      closeTool()
    } catch {
      // Mutation 错误在弹窗内统一展示。
    }
  }

  const serverColumns = [
    { title: '名称', dataIndex: 'name', key: 'name' },
    {
      title: '连接方式',
      key: 'connection',
      render: (_: unknown, server: McpServer) => (
        <Space orientation="vertical" size={0}>
          <Typography.Text>{server.transport === 'streamable_http' ? 'Streamable HTTP' : 'stdio'}</Typography.Text>
          <Typography.Text type="secondary">
            {server.endpoint ?? [server.command, ...server.args].join(' ')}
          </Typography.Text>
        </Space>
      ),
    },
    {
      title: '凭据',
      dataIndex: 'has_credentials',
      key: 'credentials',
      render: (configured: boolean) => (
        <Tag color={configured ? 'success' : 'default'}>
          {configured ? '凭据已配置' : '未配置凭据'}
        </Tag>
      ),
    },
    {
      title: '状态',
      dataIndex: 'enabled',
      key: 'status',
      render: (enabled: boolean) => (
        <Tag color={enabled ? 'success' : 'default'}>{enabled ? '已启用' : '已禁用'}</Tag>
      ),
    },
    ...(canManageWorkspace ? [{
      title: '操作',
      key: 'action',
      render: (_: unknown, server: McpServer) => (
        <Button
          loading={setServerEnabled.isPending}
          onClick={() => setServerEnabled.mutate({ serverId: server.id, enabled: !server.enabled })}
        >
          {server.enabled ? '禁用' : '启用'}
        </Button>
      ),
    }] : []),
  ]

  const toolColumns = [
    { title: '名称', dataIndex: 'name', key: 'name' },
    {
      title: '所属 Server',
      dataIndex: 'server_id',
      key: 'server',
      render: (serverId: string) => serverById.get(serverId)?.name ?? serverId,
    },
    { title: '说明', dataIndex: 'description', key: 'description' },
    {
      title: '风险',
      dataIndex: 'risk_level',
      key: 'risk',
      render: (risk: ToolRiskLevel) => <Tag color={riskColors[risk]}>{riskLabels[risk]}</Tag>,
    },
    {
      title: '状态',
      dataIndex: 'enabled',
      key: 'status',
      render: (enabled: boolean) => (
        <Tag color={enabled ? 'success' : 'default'}>{enabled ? '已启用' : '已禁用'}</Tag>
      ),
    },
    ...(canManageWorkspace ? [{
      title: '操作',
      key: 'action',
      render: (_: unknown, tool: Tool) => (
        <Button
          loading={setToolEnabled.isPending}
          onClick={() => setToolEnabled.mutate({ toolId: tool.id, enabled: !tool.enabled })}
        >
          {tool.enabled ? '禁用' : '启用'}
        </Button>
      ),
    }] : []),
  ]

  return (
    <section>
      <Flex align="center" justify="space-between" gap={16}>
        <div>
          <Typography.Title level={2}>工具与 MCP</Typography.Title>
          <Typography.Text type="secondary">管理企业 MCP Server 和数字员工可调用的 Tool</Typography.Text>
        </div>
        {canManageWorkspace && (
          <Space>
            <Button type="primary" onClick={() => setServerOpen(true)}>注册 MCP Server</Button>
            <Button disabled={!servers.data?.length} onClick={() => setToolOpen(true)}>登记 Tool</Button>
          </Space>
        )}
      </Flex>

      <Card className="tool-registry-card" title="MCP Servers">
        {servers.data?.length ? (
          <Table<McpServer>
            rowKey="id"
            pagination={false}
            dataSource={servers.data}
            columns={serverColumns}
          />
        ) : (
          <Empty description="还没有 MCP Server" />
        )}
      </Card>

      <Card className="tool-registry-card" title="Tools">
        {tools.data?.length ? (
          <Table<Tool>
            rowKey="id"
            pagination={false}
            dataSource={tools.data}
            columns={toolColumns}
          />
        ) : (
          <Empty description="还没有 Tool" />
        )}
      </Card>

      <Modal
        title="注册 MCP Server"
        open={serverOpen}
        okText="注册 Server"
        cancelText="取消"
        confirmLoading={createServer.isPending}
        onOk={() => serverForm.submit()}
        onCancel={closeServer}
      >
        {createServer.isError && (
          <Alert type="error" showIcon title={getApiErrorMessage(createServer.error, 'Server 注册失败')} />
        )}
        <Form<ServerFormValues>
          form={serverForm}
          layout="vertical"
          initialValues={{ transport: 'streamable_http', args: [] }}
          onFinish={submitServer}
        >
          <Form.Item htmlFor="mcp-server-name" label="Server 名称" name="name" rules={[{ required: true }]}>
            <Input id="mcp-server-name" maxLength={100} />
          </Form.Item>
          <Form.Item label="传输方式" name="transport" rules={[{ required: true }]}>
            <Select options={[
              { value: 'streamable_http', label: 'Streamable HTTP' },
              { value: 'stdio', label: 'stdio' },
            ]} />
          </Form.Item>
          {transport === 'streamable_http' ? (
            <Form.Item label="服务地址" name="endpoint" rules={[{ required: true, type: 'url' }]}>
              <Input placeholder="https://mcp.example.com/api" />
            </Form.Item>
          ) : (
            <>
              <Form.Item label="启动命令" name="command" rules={[{ required: true }]}>
                <Input placeholder="uvx" />
              </Form.Item>
              <Form.Item label="启动参数" name="args">
                <Select mode="tags" tokenSeparators={[' ']} placeholder="输入参数后按回车" />
              </Form.Item>
            </>
          )}
          <Form.Item
            label="凭据引用"
            name="secretReference"
            rules={[{
              pattern: /^[A-Za-z][A-Za-z0-9+.-]*:\/\/\S+$/,
              message: '请输入 URI 格式的凭据引用',
            }]}
            extra="仅保存密钥服务中的 URI 引用，凭据内容不会回显"
          >
            <Input placeholder="vault://tenants/acme/mcp/server" />
          </Form.Item>
        </Form>
      </Modal>

      <Modal
        title="登记 Tool"
        open={toolOpen}
        okText="登记 Tool"
        cancelText="取消"
        confirmLoading={createTool.isPending}
        onOk={() => toolForm.submit()}
        onCancel={closeTool}
      >
        {createTool.isError && (
          <Alert type="error" showIcon title={getApiErrorMessage(createTool.error, 'Tool 登记失败')} />
        )}
        <Form<ToolFormValues>
          form={toolForm}
          layout="vertical"
          initialValues={{ inputSchema: '{\n  "type": "object"\n}', riskLevel: 'read' }}
          onFinish={submitTool}
        >
          <Form.Item label="所属 Server" name="serverId" rules={[{ required: true }]}>
            <Select options={servers.data?.map((server) => ({ value: server.id, label: server.name }))} />
          </Form.Item>
          <Form.Item htmlFor="tool-name" label="Tool 名称" name="name" rules={[{ required: true }]}>
            <Input id="tool-name" maxLength={128} />
          </Form.Item>
          <Form.Item label="说明" name="description">
            <Input.TextArea rows={2} maxLength={2000} />
          </Form.Item>
          <Form.Item
            label="输入 JSON Schema"
            name="inputSchema"
            rules={[
              { required: true },
              {
                validator: async (_, value: string) => {
                  try {
                    const schema = JSON.parse(value) as unknown
                    if (!schema || typeof schema !== 'object' || Array.isArray(schema)) {
                      throw new Error()
                    }
                  } catch {
                    throw new Error('请输入有效的 JSON 对象')
                  }
                },
              },
            ]}
          >
            <Input.TextArea className="tool-schema-editor" rows={8} />
          </Form.Item>
          <Form.Item label="风险等级" name="riskLevel" rules={[{ required: true }]}>
            <Select options={Object.entries(riskLabels).map(([value, label]) => ({ value, label }))} />
          </Form.Item>
        </Form>
      </Modal>
    </section>
  )
}
