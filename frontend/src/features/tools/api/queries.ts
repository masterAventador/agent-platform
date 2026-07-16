import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'

import { tenantMutationKey } from '../../../api/tenant'
import { useActiveWorkspaceId } from '../../workspaces/store'
import {
  configureMcpServerCredentials,
  createMcpServer,
  createTool,
  deleteMcpServer,
  deleteTool,
  listMcpServers,
  listSyncReports,
  listToolInvocations,
  listTools,
  listToolVersions,
  removeMcpServerCredentials,
  rollbackTool,
  setMcpServerEnabled,
  syncMcpServer,
  testMcpServerConnection,
  updateMcpServer,
  updateTool,
  type McpServerCreate,
  type McpServerUpdate,
  type ToolCreate,
  type ToolUpdate,
} from './tools'


const toolKeys = {
  servers: (tenantId: string) => ['mcp-servers', tenantId] as const,
  tools: (tenantId: string, serverId?: string) => ['tools', tenantId, serverId ?? 'all'] as const,
  versions: (tenantId: string, toolId: string) => ['tool-versions', tenantId, toolId] as const,
  syncReports: (tenantId: string, serverId: string) =>
    ['mcp-sync-reports', tenantId, serverId] as const,
  invocations: (tenantId: string) => ['tool-invocations', tenantId] as const,
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
    data: tools.data?.filter(
      (tool) => tool.enabled && !tool.upstream_missing && enabledServerIds.has(tool.server_id),
    ),
    servers: servers.data,
    isPending: servers.isPending || tools.isPending,
  }
}

export function useToolVersions(toolId: string | null) {
  const tenantId = useActiveWorkspaceId()
  return useQuery({
    queryKey: toolKeys.versions(tenantId ?? '', toolId ?? ''),
    queryFn: () => listToolVersions(tenantId!, toolId!),
    enabled: Boolean(tenantId) && Boolean(toolId),
  })
}

export function useSyncReports(serverId: string | null) {
  const tenantId = useActiveWorkspaceId()
  return useQuery({
    queryKey: toolKeys.syncReports(tenantId ?? '', serverId ?? ''),
    queryFn: () => listSyncReports(tenantId!, serverId!),
    enabled: Boolean(tenantId) && Boolean(serverId),
  })
}

export function useToolInvocations() {
  const tenantId = useActiveWorkspaceId()
  return useQuery({
    queryKey: toolKeys.invocations(tenantId ?? ''),
    queryFn: () => listToolInvocations(tenantId!),
    enabled: Boolean(tenantId),
    refetchInterval: 15_000,
  })
}

function useToolRegistryInvalidation() {
  const tenantId = useActiveWorkspaceId()
  const queryClient = useQueryClient()
  return async () => {
    await Promise.all([
      queryClient.invalidateQueries({ queryKey: toolKeys.servers(tenantId!) }),
      queryClient.invalidateQueries({ queryKey: ['tools', tenantId!] }),
      queryClient.invalidateQueries({ queryKey: ['tool-versions', tenantId!] }),
      queryClient.invalidateQueries({ queryKey: ['mcp-sync-reports', tenantId!] }),
    ])
  }
}

export function useCreateMcpServer() {
  const tenantId = useActiveWorkspaceId()
  const invalidate = useToolRegistryInvalidation()
  return useMutation({
    mutationKey: tenantMutationKey(tenantId ?? '', 'mcp-servers', 'create'),
    mutationFn: (payload: McpServerCreate) => createMcpServer(tenantId!, payload),
    onSuccess: invalidate,
  })
}

export function useUpdateMcpServer() {
  const tenantId = useActiveWorkspaceId()
  const invalidate = useToolRegistryInvalidation()
  return useMutation({
    mutationKey: tenantMutationKey(tenantId ?? '', 'mcp-servers', 'update'),
    mutationFn: ({ serverId, payload }: { serverId: string; payload: McpServerUpdate }) =>
      updateMcpServer(tenantId!, serverId, payload),
    onSuccess: invalidate,
  })
}

export function useDeleteMcpServer() {
  const tenantId = useActiveWorkspaceId()
  const invalidate = useToolRegistryInvalidation()
  return useMutation({
    mutationKey: tenantMutationKey(tenantId ?? '', 'mcp-servers', 'delete'),
    mutationFn: ({ serverId }: { serverId: string }) => deleteMcpServer(tenantId!, serverId),
    onSuccess: invalidate,
  })
}

export function useSetMcpServerEnabled() {
  const tenantId = useActiveWorkspaceId()
  const invalidate = useToolRegistryInvalidation()
  return useMutation({
    mutationKey: tenantMutationKey(tenantId ?? '', 'mcp-servers', 'set-enabled'),
    mutationFn: ({ serverId, enabled }: { serverId: string; enabled: boolean }) =>
      setMcpServerEnabled(tenantId!, serverId, enabled),
    onSuccess: invalidate,
  })
}

export function useTestMcpServerConnection() {
  const tenantId = useActiveWorkspaceId()
  const invalidate = useToolRegistryInvalidation()
  return useMutation({
    mutationKey: tenantMutationKey(tenantId ?? '', 'mcp-servers', 'connection-test'),
    mutationFn: ({ serverId }: { serverId: string }) =>
      testMcpServerConnection(tenantId!, serverId),
    onSuccess: invalidate,
  })
}

export function useSyncMcpServer() {
  const tenantId = useActiveWorkspaceId()
  const invalidate = useToolRegistryInvalidation()
  return useMutation({
    mutationKey: tenantMutationKey(tenantId ?? '', 'mcp-servers', 'sync'),
    mutationFn: ({ serverId }: { serverId: string }) => syncMcpServer(tenantId!, serverId),
    onSuccess: invalidate,
  })
}

export function useConfigureMcpServerCredentials() {
  const tenantId = useActiveWorkspaceId()
  const invalidate = useToolRegistryInvalidation()
  return useMutation({
    mutationKey: tenantMutationKey(tenantId ?? '', 'mcp-servers', 'configure-credentials'),
    mutationFn: ({ serverId, values }: { serverId: string; values: Record<string, string> }) =>
      configureMcpServerCredentials(tenantId!, serverId, values),
    onSuccess: invalidate,
  })
}

export function useRemoveMcpServerCredentials() {
  const tenantId = useActiveWorkspaceId()
  const invalidate = useToolRegistryInvalidation()
  return useMutation({
    mutationKey: tenantMutationKey(tenantId ?? '', 'mcp-servers', 'remove-credentials'),
    mutationFn: ({ serverId }: { serverId: string }) =>
      removeMcpServerCredentials(tenantId!, serverId),
    onSuccess: invalidate,
  })
}

export function useCreateTool() {
  const tenantId = useActiveWorkspaceId()
  const invalidate = useToolRegistryInvalidation()
  return useMutation({
    mutationKey: tenantMutationKey(tenantId ?? '', 'tools', 'create'),
    mutationFn: (payload: ToolCreate) => createTool(tenantId!, payload),
    onSuccess: invalidate,
  })
}

export function useUpdateTool() {
  const tenantId = useActiveWorkspaceId()
  const invalidate = useToolRegistryInvalidation()
  return useMutation({
    mutationKey: tenantMutationKey(tenantId ?? '', 'tools', 'update'),
    mutationFn: ({ toolId, payload }: { toolId: string; payload: ToolUpdate }) =>
      updateTool(tenantId!, toolId, payload),
    onSuccess: invalidate,
  })
}

export function useSetToolEnabled() {
  const tenantId = useActiveWorkspaceId()
  const invalidate = useToolRegistryInvalidation()
  return useMutation({
    mutationKey: tenantMutationKey(tenantId ?? '', 'tools', 'set-enabled'),
    mutationFn: ({ toolId, enabled }: { toolId: string; enabled: boolean }) =>
      updateTool(tenantId!, toolId, { enabled }),
    onSuccess: invalidate,
  })
}

export function useDeleteTool() {
  const tenantId = useActiveWorkspaceId()
  const invalidate = useToolRegistryInvalidation()
  return useMutation({
    mutationKey: tenantMutationKey(tenantId ?? '', 'tools', 'delete'),
    mutationFn: ({ toolId }: { toolId: string }) => deleteTool(tenantId!, toolId),
    onSuccess: invalidate,
  })
}

export function useRollbackTool() {
  const tenantId = useActiveWorkspaceId()
  const invalidate = useToolRegistryInvalidation()
  return useMutation({
    mutationKey: tenantMutationKey(tenantId ?? '', 'tools', 'rollback'),
    mutationFn: ({ toolId, version }: { toolId: string; version: number }) =>
      rollbackTool(tenantId!, toolId, version),
    onSuccess: invalidate,
  })
}
