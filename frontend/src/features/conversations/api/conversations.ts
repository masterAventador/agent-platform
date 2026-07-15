import { apiClient } from '../../../api/client'
import { tenantRequestConfig } from '../../../api/tenant'
import type { Run } from '../../runs/api/runs'


export interface Conversation {
  id: string
  tenant_id: string
  employee_id: string
  created_by: string
  title: string
  thread_id: string
}

export interface ConversationMessage {
  id: string
  tenant_id: string
  conversation_id: string
  run_id: string | null
  sequence: number
  role: 'user' | 'assistant' | 'system' | 'error'
  content: string
  attachment_ids: string[]
}

export interface ConversationDetail extends Conversation {
  messages: ConversationMessage[]
  runs: Run[]
}

export interface AppendConversationMessageInput {
  content: string
  attachmentIds?: string[]
  dispatch?: boolean
}

export interface AppendConversationMessageResult {
  message: ConversationMessage
  run: Run | null
  run_action: 'stored' | 'started' | 'message_submitted' | 'queued_after_current'
}

export async function listConversations(tenantId: string): Promise<Conversation[]> {
  return (await apiClient.get<Conversation[]>('/conversations', tenantRequestConfig(tenantId))).data
}

export async function getConversation(
  tenantId: string,
  conversationId: string,
): Promise<ConversationDetail> {
  return (
    await apiClient.get<ConversationDetail>(
      `/conversations/${conversationId}`,
      tenantRequestConfig(tenantId),
    )
  ).data
}

export async function createConversation(
  tenantId: string,
  input: { employeeId: string; title?: string },
): Promise<Conversation> {
  return (
    await apiClient.post<Conversation>(
      '/conversations',
      { employee_id: input.employeeId, title: input.title },
      tenantRequestConfig(tenantId),
    )
  ).data
}

export async function appendConversationMessage(
  tenantId: string,
  conversationId: string,
  input: AppendConversationMessageInput,
): Promise<AppendConversationMessageResult> {
  return (
    await apiClient.post<AppendConversationMessageResult>(
      `/conversations/${conversationId}/messages`,
      {
        content: input.content,
        attachment_ids: input.attachmentIds ?? [],
        dispatch: input.dispatch ?? true,
      },
      tenantRequestConfig(tenantId),
    )
  ).data
}

export async function retryConversation(
  tenantId: string,
  conversationId: string,
  runId?: string,
): Promise<{ run: Run }> {
  return (
    await apiClient.post<{ run: Run }>(
      `/conversations/${conversationId}/retry`,
      { run_id: runId },
      tenantRequestConfig(tenantId),
    )
  ).data
}
