import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'

import { tenantMutationKey } from '../../../api/tenant'
import { useActiveWorkspaceId } from '../../workspaces/store'
import { listRunDeadLetters, replayRunDeadLetter } from './dead-letters'


const RUN_DEAD_LETTER_LIMIT = 100

export const runDeadLetterKeys = {
  list: (tenantId: string) => ['run-dead-letters', tenantId] as const,
}

export function useRunDeadLetters() {
  const tenantId = useActiveWorkspaceId()
  return useQuery({
    queryKey: runDeadLetterKeys.list(tenantId ?? ''),
    queryFn: () => listRunDeadLetters(tenantId!, RUN_DEAD_LETTER_LIMIT),
    enabled: Boolean(tenantId),
  })
}

export function useReplayRunDeadLetter() {
  const tenantId = useActiveWorkspaceId()
  const queryClient = useQueryClient()
  return useMutation({
    mutationKey: tenantMutationKey(tenantId ?? '', 'run-dead-letters', 'replay'),
    mutationFn: (deadLetterId: string) => replayRunDeadLetter(tenantId!, deadLetterId),
    onSuccess: async () => {
      await queryClient.invalidateQueries({ queryKey: runDeadLetterKeys.list(tenantId!) })
    },
  })
}
