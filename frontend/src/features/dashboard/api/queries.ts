import { useQuery } from '@tanstack/react-query'

import { useActiveWorkspaceId } from '../../workspaces/store'
import { getWorkbenchSummary } from './workbench'


export const workbenchKeys = {
  summary: (tenantId: string) => ['workbench', tenantId, 'summary'] as const,
}

export function useWorkbenchSummary() {
  const tenantId = useActiveWorkspaceId()
  return useQuery({
    queryKey: workbenchKeys.summary(tenantId ?? ''),
    queryFn: () => getWorkbenchSummary(tenantId!),
    enabled: Boolean(tenantId),
    refetchOnMount: 'always',
  })
}
