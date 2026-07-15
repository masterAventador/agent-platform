import { useQuery } from '@tanstack/react-query'

import { apiClient } from '../../api/client'
import { loadFrontendCapabilityModule } from './modules'
import { parseCapabilityRegistry } from './registry'

export function useCapabilityRegistry(workspaceId: string | undefined) {
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
  const capabilityIds = registry.data?.capabilities
    .map((capability) => capability.capability_id)
    .sort() ?? []
  const modules = useQuery({
    queryKey: ['capability-modules', ...capabilityIds],
    enabled: registry.isSuccess,
    queryFn: async () => Promise.all(
      capabilityIds.map(async (capabilityId) => [
        capabilityId,
        await loadFrontendCapabilityModule(capabilityId),
      ] as const),
    ),
  })

  return { registry, modules }
}
