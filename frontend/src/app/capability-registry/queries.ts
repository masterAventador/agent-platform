import { useQuery } from '@tanstack/react-query'

import { apiClient } from '../../api/client'
import { loadAuthorizedFrontendCapabilityModules } from './modules'
import { parseCapabilityRegistry } from './registry'

export function useCapabilityRegistry(
  workspaceId: string | undefined,
  userPermissions: readonly string[],
) {
  const registry = useQuery({
    queryKey: ['capability-registry', workspaceId],
    enabled: workspaceId !== undefined,
    queryFn: async () => {
      const response = await apiClient.get('/capabilities/registry', {
        headers: { 'X-Tenant-ID': workspaceId },
      })
      return parseCapabilityRegistry(response.data)
    },
  })
  const registryFingerprint = JSON.stringify(registry.data?.capabilities ?? [])
  const permissionKey = [...userPermissions].sort()
  const modules = useQuery({
    queryKey: ['capability-modules', registryFingerprint, ...permissionKey],
    enabled: registry.isSuccess,
    queryFn: async () => loadAuthorizedFrontendCapabilityModules(
      registry.data?.capabilities,
      new Set(userPermissions),
    ),
  })

  return { registry, modules }
}
