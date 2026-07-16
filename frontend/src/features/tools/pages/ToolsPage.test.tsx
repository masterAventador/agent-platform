import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import { ToolsPage } from './ToolsPage'

const testConnection = vi.fn()
const syncServer = vi.fn()
const configureCredentials = vi.fn()
const updateTool = vi.fn()
const rollbackTool = vi.fn()
const deleteTool = vi.fn()

const servers = [
  {
    id: 'server-1',
    tenant_id: 'tenant-1',
    name: '企业搜索 MCP',
    transport: 'streamable_http',
    endpoint: 'https://mcp.example.com/api',
    command: null,
    args: [],
    enabled: true,
    has_credentials: true,
    connection_status: 'failed',
    connection_tested_at: '2026-07-16T10:00:00Z',
    connection_error_code: 'mcp_timeout',
    last_synced_at: '2026-07-16T09:00:00Z',
  },
]

const tools = [
  {
    id: 'tool-1',
    tenant_id: 'tenant-1',
    server_id: 'server-1',
    name: 'search_customers',
    description: '搜索客户',
    input_schema: { type: 'object' },
    risk_level: 'external',
    approval_policy: 'risk_based',
    origin: 'discovered',
    upstream_missing: false,
    version: 3,
    enabled: true,
  },
  {
    id: 'tool-2',
    tenant_id: 'tenant-1',
    server_id: 'server-1',
    name: 'legacy_tool',
    description: '旧工具',
    input_schema: { type: 'object' },
    risk_level: 'read',
    approval_policy: 'always',
    origin: 'discovered',
    upstream_missing: true,
    version: 1,
    enabled: true,
  },
]

const invocations = [
  {
    id: 'event-1',
    event_type: 'tool.rejected',
    occurred_at: '2026-07-16T10:30:00Z',
    run_id: 'run-1',
    tool_id: 'tool-1',
    tool_name: 'search_customers',
    risk: 'external',
    reason: 'tool_disabled',
    succeeded: null,
    invocation_id: 'invocation-1',
  },
]

const versions = [
  {
    version: 3,
    description: '搜索客户',
    input_schema: { type: 'object' },
    risk_level: 'external',
    approval_policy: 'risk_based',
    change_source: 'sync',
    created_at: '2026-07-16T09:00:00Z',
  },
  {
    version: 2,
    description: '搜索客户 v2',
    input_schema: { type: 'object' },
    risk_level: 'read',
    approval_policy: 'risk_based',
    change_source: 'update',
    created_at: '2026-07-15T09:00:00Z',
  },
]

function mutationResult(mutateAsync: ReturnType<typeof vi.fn>) {
  return {
    mutate: mutateAsync,
    mutateAsync,
    isPending: false,
    isError: false,
    error: null,
    data: undefined,
    reset: vi.fn(),
  }
}

vi.mock('../api/queries', () => ({
  useMcpServers: () => ({ data: servers, isPending: false }),
  useTools: () => ({ data: tools, isPending: false }),
  useToolInvocations: () => ({ data: invocations, isPending: false }),
  useToolVersions: () => ({ data: versions, isPending: false }),
  useSyncReports: () => ({ data: [], isPending: false }),
  useCreateMcpServer: () => mutationResult(vi.fn()),
  useUpdateMcpServer: () => mutationResult(vi.fn()),
  useDeleteMcpServer: () => mutationResult(vi.fn()),
  useSetMcpServerEnabled: () => mutationResult(vi.fn()),
  useSetToolEnabled: () => mutationResult(vi.fn()),
  useTestMcpServerConnection: () => mutationResult(testConnection),
  useSyncMcpServer: () => mutationResult(syncServer),
  useConfigureMcpServerCredentials: () => mutationResult(configureCredentials),
  useRemoveMcpServerCredentials: () => mutationResult(vi.fn()),
  useCreateTool: () => mutationResult(vi.fn()),
  useUpdateTool: () => mutationResult(updateTool),
  useDeleteTool: () => mutationResult(deleteTool),
  useRollbackTool: () => mutationResult(rollbackTool),
}))

describe('ToolsPage 生命周期界面', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    testConnection.mockResolvedValue({
      status: 'ok',
      tested_at: '2026-07-16T11:00:00Z',
      tool_count: 2,
      error_code: null,
    })
    syncServer.mockResolvedValue({
      id: 'report-1',
      server_id: 'server-1',
      occurred_at: '2026-07-16T11:00:00Z',
      status: 'ok',
      added: ['fetch_order'],
      updated: ['search_customers'],
      removed: [{ name: 'send_notification', referenced: true }],
      unchanged: 1,
      error_code: null,
    })
  })

  it('展示连接状态、错误码与上游移除保护标记', () => {
    render(<ToolsPage canManageTools />)

    expect(screen.getByText('连接失败')).toBeInTheDocument()
    expect(screen.getByText(/mcp_timeout/)).toBeInTheDocument()
    expect(screen.getByText('上游已移除')).toBeInTheDocument()
    expect(screen.getAllByText('自动发现').length).toBeGreaterThan(0)
    expect(screen.getByText('v3')).toBeInTheDocument()
  })

  it('测试连接后展示最新结果', async () => {
    render(<ToolsPage canManageTools />)

    fireEvent.click(screen.getByRole('button', { name: '测试连接' }))

    await waitFor(() => expect(testConnection).toHaveBeenCalledWith(
      expect.objectContaining({ serverId: 'server-1' }),
    ))
    expect(await screen.findByText(/连接正常（发现 2 个工具）/)).toBeInTheDocument()
  })

  it('同步后弹出差异结果并标记被引用的上游移除工具', async () => {
    render(<ToolsPage canManageTools />)

    fireEvent.click(screen.getByRole('button', { name: '同步工具' }))

    await waitFor(() => expect(syncServer).toHaveBeenCalledWith(
      expect.objectContaining({ serverId: 'server-1' }),
    ))
    expect(await screen.findByText('同步结果')).toBeInTheDocument()
    expect(screen.getByText('fetch_order')).toBeInTheDocument()
    expect(screen.getByText('send_notification')).toBeInTheDocument()
    expect(screen.getByText(/仍被数字员工引用/)).toBeInTheDocument()
  })

  it('配置凭据只提交值不回显', async () => {
    configureCredentials.mockResolvedValue(servers[0])
    render(<ToolsPage canManageTools />)

    fireEvent.click(screen.getByRole('button', { name: '配置凭据' }))
    const keyInput = await screen.findByLabelText('Header 名称')
    fireEvent.change(keyInput, { target: { value: 'Authorization' } })
    fireEvent.change(screen.getByLabelText('凭据值'), {
      target: { value: 'Bearer super-secret' },
    })
    fireEvent.click(screen.getByRole('button', { name: '保存凭据' }))

    await waitFor(() => expect(configureCredentials).toHaveBeenCalledWith({
      serverId: 'server-1',
      values: { Authorization: 'Bearer super-secret' },
    }))
    await waitFor(() => {
      expect(screen.queryByDisplayValue('Bearer super-secret')).not.toBeInTheDocument()
    })
  })

  it('编辑工具提交风险等级与审批策略', async () => {
    updateTool.mockResolvedValue(tools[0])
    render(<ToolsPage canManageTools />)

    fireEvent.click(screen.getAllByRole('button', { name: /编\s*辑/ })[1])
    expect(await screen.findByText('编辑 Tool')).toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: '保存修改' }))

    await waitFor(() => expect(updateTool).toHaveBeenCalledWith(
      expect.objectContaining({ toolId: 'tool-1' }),
    ))
  })

  it('版本历史支持回滚', async () => {
    rollbackTool.mockResolvedValue(tools[0])
    render(<ToolsPage canManageTools />)

    fireEvent.click(screen.getAllByRole('button', { name: /版\s*本/ })[0])
    expect(await screen.findByText('版本历史')).toBeInTheDocument()
    expect(screen.getByText('搜索客户 v2')).toBeInTheDocument()

    fireEvent.click(screen.getByRole('button', { name: '回滚到 v2' }))
    await waitFor(() => expect(rollbackTool).toHaveBeenCalledWith({
      toolId: 'tool-1',
      version: 2,
    }))
  })

  it('展示调用记录与失败原因', () => {
    render(<ToolsPage canManageTools />)

    expect(screen.getByText('工具调用记录')).toBeInTheDocument()
    expect(screen.getByText('tool_disabled')).toBeInTheDocument()
    expect(screen.getByText('已拒绝')).toBeInTheDocument()
  })

  it('无管理权限时不显示生命周期操作按钮', () => {
    render(<ToolsPage canManageTools={false} />)

    expect(screen.queryByRole('button', { name: '测试连接' })).not.toBeInTheDocument()
    expect(screen.queryByRole('button', { name: '同步工具' })).not.toBeInTheDocument()
    expect(screen.queryByRole('button', { name: '配置凭据' })).not.toBeInTheDocument()
    expect(screen.queryByRole('button', { name: /删\s*除/ })).not.toBeInTheDocument()
  })
})
