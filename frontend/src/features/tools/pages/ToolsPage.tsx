import {
  Alert,
  Button,
  Card,
  Empty,
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

import { getApiErrorMessage } from '../../auth/api/errors'
import {
  useConfigureMcpServerCredentials,
  useCreateMcpServer,
  useCreateTool,
  useDeleteMcpServer,
  useDeleteTool,
  useMcpServers,
  useRemoveMcpServerCredentials,
  useRollbackTool,
  useSetMcpServerEnabled,
  useSetToolEnabled,
  useSyncMcpServer,
  useTestMcpServerConnection,
  useToolInvocations,
  useTools,
  useToolVersions,
  useUpdateMcpServer,
  useUpdateTool,
} from '../api/queries'
import type {
  ConnectionTestResult,
  McpServer,
  McpTransport,
  SyncReport,
  Tool,
  ToolApprovalPolicy,
  ToolRiskLevel,
} from '../api/tools'
import './tools.css'


interface ServerFormValues {
  name: string
  transport: McpTransport
  endpoint?: string
  command?: string
  args?: string[]
  secretReference?: string
}

interface ServerEditFormValues {
  name: string
  endpoint?: string
  command?: string
  args?: string[]
}

interface ToolFormValues {
  serverId: string
  name: string
  description: string
  inputSchema: string
  riskLevel: ToolRiskLevel
  approvalPolicy: ToolApprovalPolicy
}

interface ToolEditFormValues {
  description: string
  inputSchema: string
  riskLevel: ToolRiskLevel
  approvalPolicy: ToolApprovalPolicy
}

interface CredentialsFormValues {
  headerName: string
  headerValue: string
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

const approvalLabels: Record<ToolApprovalPolicy, string> = {
  risk_based: '按风险等级',
  always: '总是审批',
  never: '免审批',
}

const connectionLabels: Record<string, { label: string; color: string }> = {
  unknown: { label: '未测试', color: 'default' },
  ok: { label: '连接正常', color: 'success' },
  failed: { label: '连接失败', color: 'error' },
}

const invocationEventLabels: Record<string, string> = {
  'tool.started': '已开始',
  'tool.completed': '已完成',
  'tool.rejected': '已拒绝',
  'approval.required': '需审批',
}

export function ToolsPage({ canManageTools }: { canManageTools: boolean }) {
  const servers = useMcpServers()
  const tools = useTools()
  const invocations = useToolInvocations()
  const createServer = useCreateMcpServer()
  const updateServer = useUpdateMcpServer()
  const deleteServer = useDeleteMcpServer()
  const createTool = useCreateTool()
  const updateTool = useUpdateTool()
  const deleteTool = useDeleteTool()
  const rollbackTool = useRollbackTool()
  const setServerEnabled = useSetMcpServerEnabled()
  const setToolEnabled = useSetToolEnabled()
  const testConnection = useTestMcpServerConnection()
  const syncServer = useSyncMcpServer()
  const configureCredentials = useConfigureMcpServerCredentials()
  const removeCredentials = useRemoveMcpServerCredentials()

  const [serverOpen, setServerOpen] = useState(false)
  const [toolOpen, setToolOpen] = useState(false)
  const [editingServer, setEditingServer] = useState<McpServer | null>(null)
  const [editingTool, setEditingTool] = useState<Tool | null>(null)
  const [credentialsServer, setCredentialsServer] = useState<McpServer | null>(null)
  const [versionsTool, setVersionsTool] = useState<Tool | null>(null)
  const [syncReport, setSyncReport] = useState<SyncReport | null>(null)
  const [connectionResults, setConnectionResults] = useState<
    Record<string, ConnectionTestResult>
  >({})
  const [operationError, setOperationError] = useState<string | null>(null)

  const [serverForm] = Form.useForm<ServerFormValues>()
  const [serverEditForm] = Form.useForm<ServerEditFormValues>()
  const [toolForm] = Form.useForm<ToolFormValues>()
  const [toolEditForm] = Form.useForm<ToolEditFormValues>()
  const [credentialsForm] = Form.useForm<CredentialsFormValues>()
  const transport = Form.useWatch('transport', serverForm) ?? 'streamable_http'
  const serverById = new Map(servers.data?.map((server) => [server.id, server]))
  const versions = useToolVersions(versionsTool?.id ?? null)

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

  const closeToolEdit = () => {
    setEditingTool(null)
    toolEditForm.resetFields()
    updateTool.reset()
  }

  const closeServerEdit = () => {
    setEditingServer(null)
    serverEditForm.resetFields()
    updateServer.reset()
  }

  const closeCredentials = () => {
    setCredentialsServer(null)
    credentialsForm.resetFields()
    configureCredentials.reset()
  }

  const runOperation = async (operation: () => Promise<unknown>) => {
    setOperationError(null)
    try {
      await operation()
    } catch (error) {
      setOperationError(getApiErrorMessage(error, '操作失败，请稍后重试'))
    }
  }

  const submitServer = async (values: ServerFormValues) => {
    if (!canManageTools) return
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

  const submitServerEdit = async (values: ServerEditFormValues) => {
    if (!canManageTools || !editingServer) return
    try {
      await updateServer.mutateAsync({
        serverId: editingServer.id,
        payload: {
          name: values.name,
          endpoint: editingServer.transport === 'streamable_http' ? values.endpoint : undefined,
          command: editingServer.transport === 'stdio' ? values.command : undefined,
          args: editingServer.transport === 'stdio' ? (values.args ?? []) : undefined,
        },
      })
      closeServerEdit()
    } catch {
      // Mutation 错误在弹窗内统一展示。
    }
  }

  const submitTool = async (values: ToolFormValues) => {
    if (!canManageTools) return
    try {
      await createTool.mutateAsync({
        server_id: values.serverId,
        name: values.name,
        description: values.description,
        input_schema: JSON.parse(values.inputSchema) as Record<string, unknown>,
        risk_level: values.riskLevel,
        approval_policy: values.approvalPolicy,
        enabled: true,
      })
      closeTool()
    } catch {
      // Mutation 错误在弹窗内统一展示。
    }
  }

  const submitToolEdit = async (values: ToolEditFormValues) => {
    if (!canManageTools || !editingTool) return
    try {
      await updateTool.mutateAsync({
        toolId: editingTool.id,
        payload: {
          description: values.description,
          input_schema:
            editingTool.origin === 'manual'
              ? (JSON.parse(values.inputSchema) as Record<string, unknown>)
              : undefined,
          risk_level: values.riskLevel,
          approval_policy: values.approvalPolicy,
        },
      })
      closeToolEdit()
    } catch {
      // Mutation 错误在弹窗内统一展示。
    }
  }

  const submitCredentials = async (values: CredentialsFormValues) => {
    if (!canManageTools || !credentialsServer) return
    try {
      await configureCredentials.mutateAsync({
        serverId: credentialsServer.id,
        values: { [values.headerName]: values.headerValue },
      })
      closeCredentials()
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
          <Typography.Text>
            {server.transport === 'streamable_http' ? 'Streamable HTTP' : 'stdio'}
          </Typography.Text>
          <Typography.Text type="secondary">
            {server.endpoint ?? [server.command, ...server.args].join(' ')}
          </Typography.Text>
        </Space>
      ),
    },
    {
      title: '连接状态',
      key: 'connection_status',
      render: (_: unknown, server: McpServer) => {
        const status = connectionLabels[server.connection_status] ?? connectionLabels.unknown
        const latest = connectionResults[server.id]
        return (
          <Space orientation="vertical" size={0}>
            <Tag color={status.color}>{status.label}</Tag>
            {server.connection_error_code && (
              <Typography.Text type="danger">{server.connection_error_code}</Typography.Text>
            )}
            {latest && latest.status === 'ok' && (
              <Typography.Text type="success">
                连接正常（发现 {latest.tool_count ?? 0} 个工具）
              </Typography.Text>
            )}
            {latest && latest.status === 'failed' && (
              <Typography.Text type="danger">连接失败（{latest.error_code}）</Typography.Text>
            )}
          </Space>
        )
      },
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
    ...(canManageTools ? [{
      title: '操作',
      key: 'action',
      render: (_: unknown, server: McpServer) => (
        <Space wrap>
          <Button
            size="small"
            loading={testConnection.isPending}
            onClick={() => runOperation(async () => {
              const result = await testConnection.mutateAsync({ serverId: server.id })
              setConnectionResults((current) => ({ ...current, [server.id]: result }))
            })}
          >
            测试连接
          </Button>
          <Button
            size="small"
            loading={syncServer.isPending}
            onClick={() => runOperation(async () => {
              const report = await syncServer.mutateAsync({ serverId: server.id })
              setSyncReport(report)
            })}
          >
            同步工具
          </Button>
          <Button size="small" onClick={() => setCredentialsServer(server)}>配置凭据</Button>
          <Button
            size="small"
            onClick={() => {
              setEditingServer(server)
              serverEditForm.setFieldsValue({
                name: server.name,
                endpoint: server.endpoint ?? undefined,
                command: server.command ?? undefined,
                args: server.args,
              })
            }}
          >
            编辑
          </Button>
          <Button
            size="small"
            loading={setServerEnabled.isPending}
            onClick={() => setServerEnabled.mutate({
              serverId: server.id,
              enabled: !server.enabled,
            })}
          >
            {server.enabled ? '禁用' : '启用'}
          </Button>
          <Popconfirm
            title="删除该 MCP Server 及其下工具？"
            okText="删除"
            cancelText="取消"
            onConfirm={() => runOperation(() => deleteServer.mutateAsync({ serverId: server.id }))}
          >
            <Button size="small" danger>删除</Button>
          </Popconfirm>
        </Space>
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
    {
      title: '来源',
      key: 'origin',
      render: (_: unknown, tool: Tool) => (
        <Space orientation="vertical" size={0}>
          <Tag>{tool.origin === 'discovered' ? '自动发现' : '手动登记'}</Tag>
          {tool.upstream_missing && <Tag color="error">上游已移除</Tag>}
        </Space>
      ),
    },
    {
      title: '版本',
      dataIndex: 'version',
      key: 'version',
      render: (version: number) => <Typography.Text>v{version}</Typography.Text>,
    },
    {
      title: '风险',
      key: 'risk',
      render: (_: unknown, tool: Tool) => (
        <Space orientation="vertical" size={0}>
          <Tag color={riskColors[tool.risk_level]}>{riskLabels[tool.risk_level]}</Tag>
          <Typography.Text type="secondary">
            {approvalLabels[tool.approval_policy]}
          </Typography.Text>
        </Space>
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
    ...(canManageTools ? [{
      title: '操作',
      key: 'action',
      render: (_: unknown, tool: Tool) => (
        <Space wrap>
          <Button
            size="small"
            onClick={() => {
              setEditingTool(tool)
              toolEditForm.setFieldsValue({
                description: tool.description,
                inputSchema: JSON.stringify(tool.input_schema, null, 2),
                riskLevel: tool.risk_level,
                approvalPolicy: tool.approval_policy,
              })
            }}
          >
            编辑
          </Button>
          <Button size="small" onClick={() => setVersionsTool(tool)}>版本</Button>
          <Button
            size="small"
            loading={setToolEnabled.isPending}
            onClick={() => setToolEnabled.mutate({
              toolId: tool.id,
              enabled: !tool.enabled,
            })}
          >
            {tool.enabled ? '禁用' : '启用'}
          </Button>
          <Popconfirm
            title="删除该工具？被数字员工引用时会被拒绝。"
            okText="删除"
            cancelText="取消"
            onConfirm={() => runOperation(() => deleteTool.mutateAsync({ toolId: tool.id }))}
          >
            <Button size="small" danger>删除</Button>
          </Popconfirm>
        </Space>
      ),
    }] : []),
  ]

  const invocationColumns = [
    {
      title: '时间',
      dataIndex: 'occurred_at',
      key: 'occurred_at',
      render: (value: string) => new Date(value).toLocaleString(),
    },
    { title: '工具', dataIndex: 'tool_name', key: 'tool_name' },
    {
      title: '事件',
      dataIndex: 'event_type',
      key: 'event_type',
      render: (value: string, record: { succeeded: boolean | null }) => {
        if (value === 'tool.completed' && record.succeeded === false) {
          return <Tag color="error">执行失败</Tag>
        }
        const label = invocationEventLabels[value] ?? value
        const color = value === 'tool.rejected' ? 'error'
          : value === 'tool.completed' ? 'success'
            : 'default'
        return <Tag color={color}>{label}</Tag>
      },
    },
    {
      title: '失败原因',
      dataIndex: 'reason',
      key: 'reason',
      render: (reason: string | null) => reason
        ? <Typography.Text type="danger">{reason}</Typography.Text>
        : <Typography.Text type="secondary">—</Typography.Text>,
    },
  ]

  return (
    <section>
      <Flex align="center" justify="space-between" gap={16}>
        <div>
          <Typography.Title level={2}>工具与 MCP</Typography.Title>
          <Typography.Text type="secondary">管理企业 MCP Server 和数字员工可调用的 Tool</Typography.Text>
        </div>
        {canManageTools && (
          <Space>
            <Button type="primary" onClick={() => setServerOpen(true)}>注册 MCP Server</Button>
            <Button disabled={!servers.data?.length} onClick={() => setToolOpen(true)}>登记 Tool</Button>
          </Space>
        )}
      </Flex>

      {operationError && (
        <Alert
          type="error"
          showIcon
          closable
          title={operationError}
          onClose={() => setOperationError(null)}
        />
      )}

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

      <Card className="tool-registry-card" title="工具调用记录">
        {invocations.data?.length ? (
          <Table
            rowKey="id"
            pagination={false}
            dataSource={invocations.data}
            columns={invocationColumns}
          />
        ) : (
          <Empty description="还没有工具调用记录" />
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
            extra="可选：引用外部密钥服务；推荐注册后使用「配置凭据」直接托管，凭据内容不会回显"
          >
            <Input placeholder="vault://tenants/acme/mcp/server" />
          </Form.Item>
        </Form>
      </Modal>

      <Modal
        title="编辑 MCP Server"
        open={editingServer !== null}
        okText="保存修改"
        cancelText="取消"
        confirmLoading={updateServer.isPending}
        onOk={() => serverEditForm.submit()}
        onCancel={closeServerEdit}
      >
        {updateServer.isError && (
          <Alert type="error" showIcon title={getApiErrorMessage(updateServer.error, 'Server 更新失败')} />
        )}
        <Form<ServerEditFormValues>
          form={serverEditForm}
          layout="vertical"
          onFinish={submitServerEdit}
        >
          <Form.Item htmlFor="mcp-server-edit-name" label="Server 名称" name="name" rules={[{ required: true }]}>
            <Input id="mcp-server-edit-name" maxLength={100} />
          </Form.Item>
          {editingServer?.transport === 'streamable_http' ? (
            <Form.Item label="服务地址" name="endpoint" rules={[{ required: true, type: 'url' }]}>
              <Input />
            </Form.Item>
          ) : (
            <>
              <Form.Item label="启动命令" name="command" rules={[{ required: true }]}>
                <Input />
              </Form.Item>
              <Form.Item label="启动参数" name="args">
                <Select mode="tags" tokenSeparators={[' ']} />
              </Form.Item>
            </>
          )}
        </Form>
      </Modal>

      <Modal
        title="配置凭据"
        open={credentialsServer !== null}
        okText="保存凭据"
        cancelText="取消"
        confirmLoading={configureCredentials.isPending}
        onOk={() => credentialsForm.submit()}
        onCancel={closeCredentials}
      >
        {configureCredentials.isError && (
          <Alert type="error" showIcon title={getApiErrorMessage(configureCredentials.error, '凭据配置失败')} />
        )}
        <Typography.Paragraph type="secondary">
          凭据保存到平台密钥服务，仅在工具执行时短时解析，任何页面和接口都不会回显明文。
        </Typography.Paragraph>
        <Form<CredentialsFormValues>
          form={credentialsForm}
          layout="vertical"
          initialValues={{ headerName: 'Authorization' }}
          onFinish={submitCredentials}
        >
          <Form.Item
            htmlFor="credential-header-name"
            label="Header 名称"
            name="headerName"
            rules={[{ required: true }]}
          >
            <Input id="credential-header-name" maxLength={256} />
          </Form.Item>
          <Form.Item
            htmlFor="credential-header-value"
            label="凭据值"
            name="headerValue"
            rules={[{ required: true }]}
          >
            <Input.Password id="credential-header-value" maxLength={4096} visibilityToggle={false} />
          </Form.Item>
        </Form>
        {credentialsServer?.has_credentials && (
          <Button
            danger
            loading={removeCredentials.isPending}
            onClick={() => runOperation(async () => {
              await removeCredentials.mutateAsync({ serverId: credentialsServer.id })
              closeCredentials()
            })}
          >
            移除已配置凭据
          </Button>
        )}
      </Modal>

      <Modal
        title="同步结果"
        open={syncReport !== null}
        footer={null}
        onCancel={() => setSyncReport(null)}
      >
        {syncReport && (
          <Space orientation="vertical" size={12} className="sync-report">
            <Typography.Text>
              新增 {syncReport.added.length} 个、变更 {syncReport.updated.length} 个、
              上游移除 {syncReport.removed.length} 个、未变化 {syncReport.unchanged} 个
            </Typography.Text>
            {syncReport.added.length > 0 && (
              <div>
                <Typography.Text strong>新增</Typography.Text>
                <div>
                  {syncReport.added.map((name) => <Tag key={name} color="success">{name}</Tag>)}
                </div>
              </div>
            )}
            {syncReport.updated.length > 0 && (
              <div>
                <Typography.Text strong>变更</Typography.Text>
                <div>
                  {syncReport.updated.map((name) => <Tag key={name} color="processing">{name}</Tag>)}
                </div>
              </div>
            )}
            {syncReport.removed.length > 0 && (
              <div>
                <Typography.Text strong>上游移除</Typography.Text>
                <div>
                  {syncReport.removed.map((entry) => (
                    <div key={entry.name}>
                      <Tag color="error">{entry.name}</Tag>
                      {entry.referenced && (
                        <Typography.Text type="warning">
                          仍被数字员工引用，已标记为上游已移除并拒绝调用
                        </Typography.Text>
                      )}
                    </div>
                  ))}
                </div>
              </div>
            )}
          </Space>
        )}
      </Modal>

      <Modal
        title="编辑 Tool"
        open={editingTool !== null}
        okText="保存修改"
        cancelText="取消"
        confirmLoading={updateTool.isPending}
        onOk={() => toolEditForm.submit()}
        onCancel={closeToolEdit}
      >
        {updateTool.isError && (
          <Alert type="error" showIcon title={getApiErrorMessage(updateTool.error, 'Tool 更新失败')} />
        )}
        <Form<ToolEditFormValues>
          form={toolEditForm}
          layout="vertical"
          onFinish={submitToolEdit}
        >
          <Form.Item label="说明" name="description">
            <Input.TextArea rows={2} maxLength={2000} />
          </Form.Item>
          {editingTool?.origin === 'manual' && (
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
          )}
          <Form.Item label="风险等级" name="riskLevel" rules={[{ required: true }]}>
            <Select options={Object.entries(riskLabels).map(([value, label]) => ({ value, label }))} />
          </Form.Item>
          <Form.Item
            label="审批策略"
            name="approvalPolicy"
            rules={[{ required: true }]}
            extra="破坏性操作永远要求审批，不允许豁免"
          >
            <Select options={Object.entries(approvalLabels).map(([value, label]) => ({ value, label }))} />
          </Form.Item>
        </Form>
      </Modal>

      <Modal
        title="版本历史"
        open={versionsTool !== null}
        footer={null}
        onCancel={() => setVersionsTool(null)}
      >
        {versions.data?.length ? (
          <Space orientation="vertical" size={12} className="tool-versions">
            {[...versions.data].reverse().map((item) => (
              <Card key={item.version} size="small">
                <Flex align="center" justify="space-between" gap={12}>
                  <Space orientation="vertical" size={0}>
                    <Typography.Text strong>
                      v{item.version} · {riskLabels[item.risk_level]} · {approvalLabels[item.approval_policy]}
                    </Typography.Text>
                    <Typography.Text type="secondary">{item.description || '（无说明）'}</Typography.Text>
                    <Typography.Text type="secondary">
                      {item.change_source} · {new Date(item.created_at).toLocaleString()}
                    </Typography.Text>
                  </Space>
                  {canManageTools && versionsTool && item.version !== versionsTool.version && (
                    <Button
                      size="small"
                      loading={rollbackTool.isPending}
                      onClick={() => runOperation(async () => {
                        await rollbackTool.mutateAsync({
                          toolId: versionsTool.id,
                          version: item.version,
                        })
                        setVersionsTool(null)
                      })}
                    >
                      回滚到 v{item.version}
                    </Button>
                  )}
                </Flex>
              </Card>
            ))}
          </Space>
        ) : (
          <Empty description="暂无版本记录" />
        )}
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
          initialValues={{
            inputSchema: '{\n  "type": "object"\n}',
            riskLevel: 'read',
            approvalPolicy: 'risk_based',
          }}
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
          <Form.Item label="审批策略" name="approvalPolicy" rules={[{ required: true }]}>
            <Select options={Object.entries(approvalLabels).map(([value, label]) => ({ value, label }))} />
          </Form.Item>
        </Form>
      </Modal>
    </section>
  )
}
