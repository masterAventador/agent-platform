import { beforeEach, describe, expect, it, vi } from 'vitest'

import { apiClient } from '../../../api/client'
import { listRunDeadLetters, replayRunDeadLetter } from './dead-letters'


vi.mock('../../../api/client', () => ({
  apiClient: { get: vi.fn(), post: vi.fn() },
}))

const tenantId = '10000000-0000-4000-8000-000000000010'
const deadLetterId = '20000000-0000-4000-8000-000000000020'
const runId = '30000000-0000-4000-8000-000000000030'
const commandId = '40000000-0000-4000-8000-000000000040'
const safeSummary = {
  known_field_keys: [],
  unknown_fields: [],
  field_count: 0,
  total_bytes: 0,
  sha256: null,
}

describe('run dead letter API', () => {
  beforeEach(() => vi.clearAllMocks())

  it('校验并返回安全的死信列表', async () => {
    vi.mocked(apiClient.get).mockResolvedValue({
      data: [{
        id: deadLetterId,
        original_command_id: commandId,
        original_run_id: runId,
        action: 'start',
        attempts: 5,
        error_type: 'delivery_processing_failed',
        is_malformed: false,
        raw_fields_summary: safeSummary,
        failed_at: '2026-07-13T08:00:00Z',
        replayed_run_id: null,
        replayed_command_id: null,
        replayed_at: null,
        settled_run_id: runId,
        mirrored_at: '2026-07-13T08:01:00Z',
      }],
    })

    await expect(listRunDeadLetters(tenantId)).resolves.toHaveLength(1)
    expect(apiClient.get).toHaveBeenCalledWith('/run-dead-letters', {
      headers: { 'X-Tenant-ID': tenantId },
      params: { limit: 100 },
    })
  })

  it('拒绝协议外字段或不合法时间', async () => {
    vi.mocked(apiClient.get).mockResolvedValue({
      data: [{
        id: deadLetterId,
        original_command_id: commandId,
        original_run_id: runId,
        action: 'start',
        attempts: 5,
        error_type: 'delivery_processing_failed',
        is_malformed: false,
        raw_fields_summary: safeSummary,
        failed_at: 'not-a-date',
        replayed_run_id: null,
        replayed_command_id: null,
        replayed_at: null,
        settled_run_id: runId,
        mirrored_at: null,
        payload: { secret: true },
      }],
    })

    await expect(listRunDeadLetters(tenantId)).rejects.toBeDefined()
  })

  it('拒绝摘要中的额外敏感字段和伪造哈希', async () => {
    vi.mocked(apiClient.get).mockResolvedValue({
      data: [{
        id: deadLetterId,
        original_command_id: null,
        original_run_id: null,
        action: null,
        attempts: 5,
        error_type: 'malformed_queue_message',
        is_malformed: true,
        raw_fields_summary: {
          ...safeSummary,
          known_field_keys: ['payload'],
          sha256: 'not-a-sha256',
          raw_value: 'must-not-enter-the-client',
        },
        failed_at: '2026-07-13T08:00:00Z',
        replayed_run_id: null,
        replayed_command_id: null,
        replayed_at: null,
        settled_run_id: null,
        mirrored_at: null,
      }],
    })

    await expect(listRunDeadLetters(tenantId)).rejects.toBeDefined()
  })

  it('校验重放响应并保持请求体为空', async () => {
    vi.mocked(apiClient.post).mockResolvedValue({ data: { run_id: runId, command_id: commandId } })

    await expect(replayRunDeadLetter(tenantId, deadLetterId)).resolves.toEqual({
      run_id: runId,
      command_id: commandId,
    })
    expect(apiClient.post).toHaveBeenCalledWith(
      `/run-dead-letters/${deadLetterId}/replay`,
      undefined,
      { headers: { 'X-Tenant-ID': tenantId } },
    )
  })
})
