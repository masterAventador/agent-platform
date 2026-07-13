import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'

import { useActiveWorkspaceId } from '../../workspaces/store'
import {
  createMcpServer,
  createTool,
  listMcpServers,
  listTools,
  setMcpServerEnabled,
  setToolEnabled,
  type McpServerCreate,
  type ToolCreate,
} from './tools'


const toolKeys = {
  servers: (tenantId: string) => ['mcp-servers', tenantId] as const,
  tools: (tenantId: string, serverId?: string) => ['tools', tenantId, serverId ?? 'all'] as const,
}

export function useMcpServers() {
  const tenantId = useActiveWorkspaceId()
  return useQuery({
    queryKey: toolKeys.servers(tenantId ?? ''),
    queryFn: () => listMcpServers(tenantId!),
    enabled: Boolean(tenantId),
  })
}

export function useTools(serverId?: string) {
  const tenantId = useActiveWorkspaceId()
  return useQuery({
    queryKey: toolKeys.tools(tenantId ?? '', serverId),
    queryFn: () => listTools(tenantId!, serverId),
    enabled: Boolean(tenantId),
  })
}

export function useAvailableTools() {
  const servers = useMcpServers()
  const tools = useTools()
  const enabledServerIds = new Set(
    servers.data?.filter((server) => server.enabled).map((server) => server.id),
  )
  return {
    data: tools.data?.filter((tool) => tool.enabled && enabledServerIds.has(tool.server_id)),
    servers: servers.data,
    isPending: servers.isPending || tools.isPending,
  }
}

export function useCreateMcpServer() {
  const tenantId = useActiveWorkspaceId()
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: (payload: McpServerCreate) => createMcpServer(tenantId!, payload),
    onSuccess: async () => queryClient.invalidateQueries({ queryKey: toolKeys.servers(tenantId!) }),
  })
}

export function useSetMcpServerEnabled() {
  const tenantId = useActiveWorkspaceId()
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: ({ serverId, enabled }: { serverId: string; enabled: boolean }) =>
      setMcpServerEnabled(tenantId!, serverId, enabled),
    onSuccess: async () => queryClient.invalidateQueries({ queryKey: toolKeys.servers(tenantId!) }),
  })
}

export function useCreateTool() {
  const tenantId = useActiveWorkspaceId()
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: (payload: ToolCreate) => createTool(tenantId!, payload),
    onSuccess: async () => queryClient.invalidateQueries({ queryKey: toolKeys.tools(tenantId!) }),
  })
}

export function useSetToolEnabled() {
  const tenantId = useActiveWorkspaceId()
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: ({ toolId, enabled }: { toolId: string; enabled: boolean }) =>
      setToolEnabled(tenantId!, toolId, enabled),
    onSuccess: async () => queryClient.invalidateQueries({ queryKey: toolKeys.tools(tenantId!) }),
  })
}
