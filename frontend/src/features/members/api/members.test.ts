import { beforeEach, describe, expect, it, vi } from 'vitest'

import { apiClient } from '../../../api/client'
import {
  changeMemberRole,
  createInvitation,
  listMembers,
  transferOwner,
  updateTenantSettings,
} from './members'

vi.mock('../../../api/client', () => ({
  apiClient: { get: vi.fn(), post: vi.fn(), patch: vi.fn(), delete: vi.fn() },
}))

const tenantId = '10000000-0000-4000-8000-000000000010'
const userId = '20000000-0000-4000-8000-000000000020'

describe('members API', () => {
  beforeEach(() => vi.clearAllMocks())

  it('lists members with the tenant header and validates roles', async () => {
    vi.mocked(apiClient.get).mockResolvedValue({
      data: [{
        user_id: userId,
        email: 'owner@example.com',
        display_name: null,
        role: 'owner',
        joined_at: '2026-07-17T08:00:00Z',
      }],
    })

    await expect(listMembers(tenantId)).resolves.toMatchObject([{ role: 'owner' }])
    expect(apiClient.get).toHaveBeenCalledWith('/tenant/members', {
      headers: { 'X-Tenant-ID': tenantId },
    })
  })

  it('changes a member role', async () => {
    vi.mocked(apiClient.patch).mockResolvedValue({
      data: {
        user_id: userId,
        email: 'member@example.com',
        display_name: null,
        role: 'admin',
        joined_at: '2026-07-17T08:00:00Z',
      },
    })

    await expect(changeMemberRole(tenantId, userId, 'admin')).resolves.toMatchObject({
      role: 'admin',
    })
    expect(apiClient.patch).toHaveBeenCalledWith(
      `/tenant/members/${userId}/role`,
      { role: 'admin' },
      { headers: { 'X-Tenant-ID': tenantId } },
    )
  })

  it('transfers ownership', async () => {
    vi.mocked(apiClient.post).mockResolvedValue({ data: { status: 'ok' } })
    await transferOwner(tenantId, userId)
    expect(apiClient.post).toHaveBeenCalledWith(
      '/tenant/members/transfer-owner',
      { user_id: userId },
      { headers: { 'X-Tenant-ID': tenantId } },
    )
  })

  it('creates an invitation and returns the raw token', async () => {
    vi.mocked(apiClient.post).mockResolvedValue({
      data: {
        id: '30000000-0000-4000-8000-000000000030',
        email: 'invitee@example.com',
        role: 'member',
        status: 'pending',
        created_at: '2026-07-17T08:00:00Z',
        expires_at: '2026-07-24T08:00:00Z',
        token: 'raw-token-value',
      },
    })

    await expect(createInvitation(tenantId, 'invitee@example.com', 'member')).resolves.toMatchObject(
      { token: 'raw-token-value', status: 'pending' },
    )
  })

  it('renames the tenant', async () => {
    vi.mocked(apiClient.patch).mockResolvedValue({
      data: { id: tenantId, name: 'Acme', slug: 'acme' },
    })
    await expect(updateTenantSettings(tenantId, 'Acme')).resolves.toMatchObject({ name: 'Acme' })
  })
})
