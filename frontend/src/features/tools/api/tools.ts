import { apiClient } from '../../../api/client'
import { tenantRequestConfig } from '../../../api/tenant'


export type McpTransport = 'streamable_http' | 'stdio'
export type ToolRiskLevel = 'read' | 'write' | 'external' | 'destructive'
export type ToolApprovalPolicy = 'risk_based' | 'always' | 'never'
export type ToolOrigin = 'manual' | 'discovered'
export type McpConnectionStatus = 'unknown' | 'ok' | 'failed'

export interface McpServer {
  id: string
  tenant_id: string
  name: string
  transport: McpTransport
  endpoint: string | null
  command: string | null
  args: string[]
  enabled: boolean
  has_credentials: boolean
  connection_status: McpConnectionStatus
  connection_tested_at: string | null
  connection_error_code: string | null
  last_synced_at: string | null
}

export interface McpServerCreate {
  name: string
  transport: McpTransport
  endpoint?: string
  command?: string
  args: string[]
  secret_reference?: string
  enabled: boolean
}

export interface McpServerUpdate {
  name?: string
  endpoint?: string
  command?: string
  args?: string[]
  enabled?: boolean
}

export interface ConnectionTestResult {
  status: McpConnectionStatus
  tested_at: string
  tool_count: number | null
  error_code: string | null
}

export interface SyncRemovedEntry {
  name: string
  referenced: boolean
}

export interface SyncReport {
  id: string
  server_id: string
  occurred_at: string
  status: 'ok' | 'failed'
  added: string[]
  updated: string[]
  removed: SyncRemovedEntry[]
  unchanged: number
  error_code: string | null
}

export interface Tool {
  id: string
  tenant_id: string
  server_id: string
  name: string
  description: string
  input_schema: Record<string, unknown>
  risk_level: ToolRiskLevel
  approval_policy: ToolApprovalPolicy
  origin: ToolOrigin
  upstream_missing: boolean
  version: number
  enabled: boolean
}

export interface ToolCreate {
  server_id: string
  name: string
  description: string
  input_schema: Record<string, unknown>
  risk_level: ToolRiskLevel
  approval_policy?: ToolApprovalPolicy
  enabled: boolean
}

export interface ToolUpdate {
  description?: string
  input_schema?: Record<string, unknown>
  risk_level?: ToolRiskLevel
  approval_policy?: ToolApprovalPolicy
  enabled?: boolean
}

export interface ToolVersion {
  version: number
  description: string
  input_schema: Record<string, unknown>
  risk_level: ToolRiskLevel
  approval_policy: ToolApprovalPolicy
  change_source: string
  created_at: string
}

export interface ToolReference {
  tool_id: string
  employee_id: string
  employee_name: string
  relation: 'employee_draft' | 'employee_version'
  version: number | null
}

export interface ToolInvocation {
  id: string
  event_type: string
  occurred_at: string
  run_id: string
  tool_id: string
  tool_name: string
  risk: string | null
  reason: string | null
  succeeded: boolean | null
  invocation_id: string | null
}

export async function listMcpServers(tenantId: string): Promise<McpServer[]> {
  return (await apiClient.get<McpServer[]>('/mcp-servers', {
    ...tenantRequestConfig(tenantId),
  })).data
}

export async function createMcpServer(
  tenantId: string,
  payload: McpServerCreate,
): Promise<McpServer> {
  return (await apiClient.post<McpServer>('/mcp-servers', payload, {
    ...tenantRequestConfig(tenantId),
  })).data
}

export async function updateMcpServer(
  tenantId: string,
  serverId: string,
  payload: McpServerUpdate,
): Promise<McpServer> {
  return (await apiClient.patch<McpServer>(`/mcp-servers/${serverId}`, payload, {
    ...tenantRequestConfig(tenantId),
  })).data
}

export async function deleteMcpServer(tenantId: string, serverId: string): Promise<void> {
  await apiClient.delete(`/mcp-servers/${serverId}`, {
    ...tenantRequestConfig(tenantId),
  })
}

export async function setMcpServerEnabled(
  tenantId: string,
  serverId: string,
  enabled: boolean,
): Promise<McpServer> {
  return updateMcpServer(tenantId, serverId, { enabled })
}

export async function testMcpServerConnection(
  tenantId: string,
  serverId: string,
): Promise<ConnectionTestResult> {
  return (await apiClient.post<ConnectionTestResult>(
    `/mcp-servers/${serverId}/connection-test`,
    undefined,
    { ...tenantRequestConfig(tenantId) },
  )).data
}

export async function syncMcpServer(
  tenantId: string,
  serverId: string,
): Promise<SyncReport> {
  return (await apiClient.post<SyncReport>(`/mcp-servers/${serverId}/sync`, undefined, {
    ...tenantRequestConfig(tenantId),
  })).data
}

export async function listSyncReports(
  tenantId: string,
  serverId: string,
): Promise<SyncReport[]> {
  return (await apiClient.get<SyncReport[]>(`/mcp-servers/${serverId}/sync-reports`, {
    ...tenantRequestConfig(tenantId),
  })).data
}

export async function configureMcpServerCredentials(
  tenantId: string,
  serverId: string,
  values: Record<string, string>,
): Promise<McpServer> {
  return (await apiClient.put<McpServer>(
    `/mcp-servers/${serverId}/credentials`,
    { values },
    { ...tenantRequestConfig(tenantId) },
  )).data
}

export async function removeMcpServerCredentials(
  tenantId: string,
  serverId: string,
): Promise<McpServer> {
  return (await apiClient.delete<McpServer>(`/mcp-servers/${serverId}/credentials`, {
    ...tenantRequestConfig(tenantId),
  })).data
}

export async function listTools(tenantId: string, serverId?: string): Promise<Tool[]> {
  return (await apiClient.get<Tool[]>('/tools', {
    ...tenantRequestConfig(tenantId),
    params: serverId ? { server_id: serverId } : undefined,
  })).data
}

export async function createTool(tenantId: string, payload: ToolCreate): Promise<Tool> {
  return (await apiClient.post<Tool>('/tools', payload, {
    ...tenantRequestConfig(tenantId),
  })).data
}

export async function updateTool(
  tenantId: string,
  toolId: string,
  payload: ToolUpdate,
): Promise<Tool> {
  return (await apiClient.patch<Tool>(`/tools/${toolId}`, payload, {
    ...tenantRequestConfig(tenantId),
  })).data
}

export async function deleteTool(tenantId: string, toolId: string): Promise<void> {
  await apiClient.delete(`/tools/${toolId}`, {
    ...tenantRequestConfig(tenantId),
  })
}

export async function setToolEnabled(
  tenantId: string,
  toolId: string,
  enabled: boolean,
): Promise<Tool> {
  return updateTool(tenantId, toolId, { enabled })
}

export async function listToolVersions(
  tenantId: string,
  toolId: string,
): Promise<ToolVersion[]> {
  return (await apiClient.get<ToolVersion[]>(`/tools/${toolId}/versions`, {
    ...tenantRequestConfig(tenantId),
  })).data
}

export async function rollbackTool(
  tenantId: string,
  toolId: string,
  version: number,
): Promise<Tool> {
  return (await apiClient.post<Tool>(`/tools/${toolId}/rollback`, { version }, {
    ...tenantRequestConfig(tenantId),
  })).data
}

export async function listToolReferences(
  tenantId: string,
  toolId: string,
): Promise<ToolReference[]> {
  return (await apiClient.get<ToolReference[]>(`/tools/${toolId}/references`, {
    ...tenantRequestConfig(tenantId),
  })).data
}

export async function listToolInvocations(
  tenantId: string,
  params?: { toolId?: string; serverId?: string; limit?: number },
): Promise<ToolInvocation[]> {
  return (await apiClient.get<ToolInvocation[]>('/tool-invocations', {
    ...tenantRequestConfig(tenantId),
    params: {
      tool_id: params?.toolId,
      server_id: params?.serverId,
      limit: params?.limit,
    },
  })).data
}
