import { beforeEach, describe, expect, it, vi } from 'vitest'

import { apiClient } from '../../../api/client'
import {
  createMemory,
  deleteMemory,
  listMemories,
  updateMemory,
} from './memories'


vi.mock('../../../api/client', () => ({
  apiClient: {
    get: vi.fn(),
    post: vi.fn(),
    patch: vi.fn(),
    delete: vi.fn(),
  },
}))

describe('memory API boundary', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    vi.mocked(apiClient.get).mockResolvedValue({ data: [] })
    vi.mocked(apiClient.post).mockResolvedValue({ data: { id: 'memory-1' } })
    vi.mocked(apiClient.patch).mockResolvedValue({ data: { id: 'memory-1' } })
    vi.mocked(apiClient.delete).mockResolvedValue({ data: undefined })
  })

  it('lists memories with tenant scope and optional filters', async () => {
    await listMemories('tenant-1', {})
    await listMemories('tenant-1', { scope: 'user', q: '签名', activeOnly: true })

    expect(apiClient.get).toHaveBeenNthCalledWith(1, '/memories', {
      headers: { 'X-Tenant-ID': 'tenant-1' },
      params: {},
    })
    expect(apiClient.get).toHaveBeenNthCalledWith(2, '/memories', {
      headers: { 'X-Tenant-ID': 'tenant-1' },
      params: { scope: 'user', q: '签名', active_only: true },
    })
  })

  it('creates, corrects, toggles and deletes memories against tenant endpoints', async () => {
    await createMemory('tenant-1', { scope: 'user', content: '偏好中文签名' })
    await updateMemory('tenant-1', 'memory-1', { content: '偏好英文签名' })
    await updateMemory('tenant-1', 'memory-1', { status: 'disabled' })
    await deleteMemory('tenant-1', 'memory-1')

    expect(apiClient.post).toHaveBeenCalledWith(
      '/memories',
      { scope: 'user', content: '偏好中文签名' },
      { headers: { 'X-Tenant-ID': 'tenant-1' } },
    )
    expect(apiClient.patch).toHaveBeenNthCalledWith(
      1,
      '/memories/memory-1',
      { content: '偏好英文签名' },
      { headers: { 'X-Tenant-ID': 'tenant-1' } },
    )
    expect(apiClient.patch).toHaveBeenNthCalledWith(
      2,
      '/memories/memory-1',
      { status: 'disabled' },
      { headers: { 'X-Tenant-ID': 'tenant-1' } },
    )
    expect(apiClient.delete).toHaveBeenCalledWith('/memories/memory-1', {
      headers: { 'X-Tenant-ID': 'tenant-1' },
    })
  })

  it('passes scope_ref when creating namespaced memories', async () => {
    await createMemory('tenant-1', {
      scope: 'employee',
      scopeRef: 'employee-9',
      content: '员工经验',
    })

    expect(apiClient.post).toHaveBeenCalledWith(
      '/memories',
      { scope: 'employee', scope_ref: 'employee-9', content: '员工经验' },
      { headers: { 'X-Tenant-ID': 'tenant-1' } },
    )
  })
})
