import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'

import { tenantMutationKey } from '../../../api/tenant'
import { runKeys } from '../../runs/api/queries'
import { controlRun } from '../../runs/api/runs'
import { useActiveWorkspaceId } from '../../workspaces/store'
import {
  appendConversationMessage,
  createConversation,
  getConversation,
  listConversations,
  retryConversation,
  type AppendConversationMessageInput,
  type ConversationDetail,
} from './conversations'


const terminalRunStatuses = new Set(['completed', 'failed', 'cancelled'])

function hasActiveRun(detail: ConversationDetail | undefined): boolean {
  return Boolean(detail?.runs.some((run) => !terminalRunStatuses.has(run.status)))
}


export const conversationKeys = {
  all: (tenantId: string) => ['conversations', tenantId] as const,
  detail: (tenantId: string, conversationId: string) => [
    'conversations',
    tenantId,
    conversationId,
  ] as const,
}

export function useConversations() {
  const tenantId = useActiveWorkspaceId()
  return useQuery({
    queryKey: conversationKeys.all(tenantId ?? ''),
    queryFn: () => listConversations(tenantId!),
    enabled: Boolean(tenantId),
  })
}

export function useConversation(conversationId: string | undefined) {
  const tenantId = useActiveWorkspaceId()
  return useQuery({
    queryKey: conversationKeys.detail(tenantId ?? '', conversationId ?? ''),
    queryFn: () => getConversation(tenantId!, conversationId!),
    enabled: Boolean(tenantId && conversationId),
    // 存在活跃关联任务时轮询刷新，让自动续跑轮次与取消结果及时反映到时间线
    refetchInterval: (query) => (hasActiveRun(query.state.data) ? 2000 : false),
  })
}

export function useCreateConversation() {
  const tenantId = useActiveWorkspaceId()
  const queryClient = useQueryClient()
  return useMutation({
    mutationKey: tenantMutationKey(tenantId ?? '', 'conversations', 'create'),
    mutationFn: (input: { employeeId: string; title?: string }) => (
      createConversation(tenantId!, input)
    ),
    onSuccess: async () => queryClient.invalidateQueries({
      queryKey: conversationKeys.all(tenantId!),
    }),
  })
}

export function useAppendConversationMessage(conversationId: string) {
  const tenantId = useActiveWorkspaceId()
  const queryClient = useQueryClient()
  return useMutation({
    mutationKey: tenantMutationKey(
      tenantId ?? '',
      'conversations',
      'append-message',
      conversationId,
    ),
    mutationFn: (input: AppendConversationMessageInput) => (
      appendConversationMessage(tenantId!, conversationId, input)
    ),
    onSuccess: async () => {
      await Promise.all([
        queryClient.invalidateQueries({ queryKey: conversationKeys.all(tenantId!) }),
        queryClient.invalidateQueries({
          queryKey: conversationKeys.detail(tenantId!, conversationId),
        }),
      ])
    },
  })
}

export function useRetryConversation(conversationId: string) {
  const tenantId = useActiveWorkspaceId()
  const queryClient = useQueryClient()
  return useMutation({
    mutationKey: tenantMutationKey(
      tenantId ?? '',
      'conversations',
      'retry',
      conversationId,
    ),
    mutationFn: (runId?: string) => retryConversation(tenantId!, conversationId, runId),
    onSuccess: async () => {
      await Promise.all([
        queryClient.invalidateQueries({ queryKey: conversationKeys.all(tenantId!) }),
        queryClient.invalidateQueries({
          queryKey: conversationKeys.detail(tenantId!, conversationId),
        }),
      ])
    },
  })
}


export function useCancelConversationRun(conversationId: string) {
  const tenantId = useActiveWorkspaceId()
  const queryClient = useQueryClient()
  return useMutation({
    mutationKey: tenantMutationKey(
      tenantId ?? '',
      'conversations',
      'cancel-run',
      conversationId,
    ),
    mutationFn: (runId: string) => controlRun(tenantId!, runId, 'cancel'),
    onSuccess: async () => {
      await Promise.all([
        queryClient.invalidateQueries({ queryKey: runKeys.all(tenantId!) }),
        queryClient.invalidateQueries({
          queryKey: conversationKeys.detail(tenantId!, conversationId),
        }),
      ])
    },
  })
}
