import { beforeEach, describe, expect, it, vi } from 'vitest'

import { apiClient } from '../../../api/client'
import {
  appendConversationMessage,
  createConversation,
  getConversation,
  listConversations,
  retryConversation,
} from './conversations'


vi.mock('../../../api/client', () => ({
  apiClient: {
    get: vi.fn(),
    post: vi.fn(),
  },
}))

describe('conversation API boundary', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    vi.mocked(apiClient.get).mockResolvedValue({ data: [] })
    vi.mocked(apiClient.post).mockResolvedValue({ data: { id: 'conversation-1' } })
  })

  it('uses tenant-scoped endpoints for list and detail', async () => {
    await listConversations('tenant-1')
    await getConversation('tenant-1', 'conversation-1')

    expect(apiClient.get).toHaveBeenNthCalledWith(
      1,
      '/conversations',
      { headers: { 'X-Tenant-ID': 'tenant-1' } },
    )
    expect(apiClient.get).toHaveBeenNthCalledWith(
      2,
      '/conversations/conversation-1',
      { headers: { 'X-Tenant-ID': 'tenant-1' } },
    )
  })

  it('creates conversations, appends real message content, and retries failed runs', async () => {
    await createConversation('tenant-1', {
      employeeId: 'employee-1',
      title: '竞品调研',
    })
    await appendConversationMessage('tenant-1', 'conversation-1', {
      content: '继续分析',
      attachmentIds: ['file-1'],
      dispatch: true,
    })
    await retryConversation('tenant-1', 'conversation-1', 'run-1')

    expect(apiClient.post).toHaveBeenNthCalledWith(
      1,
      '/conversations',
      { employee_id: 'employee-1', title: '竞品调研' },
      { headers: { 'X-Tenant-ID': 'tenant-1' } },
    )
    expect(apiClient.post).toHaveBeenNthCalledWith(
      2,
      '/conversations/conversation-1/messages',
      { content: '继续分析', attachment_ids: ['file-1'], dispatch: true },
      { headers: { 'X-Tenant-ID': 'tenant-1' } },
    )
    expect(apiClient.post).toHaveBeenNthCalledWith(
      3,
      '/conversations/conversation-1/retry',
      { run_id: 'run-1' },
      { headers: { 'X-Tenant-ID': 'tenant-1' } },
    )
  })
})
