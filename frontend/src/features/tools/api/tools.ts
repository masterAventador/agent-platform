import { apiClient } from '../../../api/client'


export type McpTransport = 'streamable_http' | 'stdio'
export type ToolRiskLevel = 'read' | 'write' | 'external' | 'destructive'

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

export interface Tool {
  id: string
  tenant_id: string
  server_id: string
  name: string
  description: string
  input_schema: Record<string, unknown>
  risk_level: ToolRiskLevel
  enabled: boolean
}

export interface ToolCreate {
  server_id: string
  name: string
  description: string
  input_schema: Record<string, unknown>
  risk_level: ToolRiskLevel
  enabled: boolean
}

function tenantHeaders(tenantId: string) {
  return { 'X-Tenant-ID': tenantId }
}

export async function listMcpServers(tenantId: string): Promise<McpServer[]> {
  return (await apiClient.get<McpServer[]>('/mcp-servers', {
    headers: tenantHeaders(tenantId),
  })).data
}

export async function createMcpServer(
  tenantId: string,
  payload: McpServerCreate,
): Promise<McpServer> {
  return (await apiClient.post<McpServer>('/mcp-servers', payload, {
    headers: tenantHeaders(tenantId),
  })).data
}

export async function setMcpServerEnabled(
  tenantId: string,
  serverId: string,
  enabled: boolean,
): Promise<McpServer> {
  return (await apiClient.patch<McpServer>(`/mcp-servers/${serverId}`, { enabled }, {
    headers: tenantHeaders(tenantId),
  })).data
}

export async function listTools(tenantId: string, serverId?: string): Promise<Tool[]> {
  return (await apiClient.get<Tool[]>('/tools', {
    headers: tenantHeaders(tenantId),
    params: serverId ? { server_id: serverId } : undefined,
  })).data
}

export async function createTool(tenantId: string, payload: ToolCreate): Promise<Tool> {
  return (await apiClient.post<Tool>('/tools', payload, {
    headers: tenantHeaders(tenantId),
  })).data
}

export async function setToolEnabled(
  tenantId: string,
  toolId: string,
  enabled: boolean,
): Promise<Tool> {
  return (await apiClient.patch<Tool>(`/tools/${toolId}`, { enabled }, {
    headers: tenantHeaders(tenantId),
  })).data
}
