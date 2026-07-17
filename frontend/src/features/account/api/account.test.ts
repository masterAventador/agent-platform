import { beforeEach, describe, expect, it, vi } from 'vitest'

import { apiClient } from '../../../api/client'
import {
  changePassword,
  getProfile,
  listSessions,
  requestEmailVerification,
  revokeOtherSessions,
  updateProfile,
} from './account'

vi.mock('../../../api/client', () => ({
  apiClient: { get: vi.fn(), post: vi.fn(), patch: vi.fn(), delete: vi.fn() },
}))

const userId = '20000000-0000-4000-8000-000000000020'

describe('account API', () => {
  beforeEach(() => vi.clearAllMocks())

  it('reads the profile', async () => {
    vi.mocked(apiClient.get).mockResolvedValue({
      data: { id: userId, email: 'user@example.com', display_name: null, email_verified: false },
    })
    await expect(getProfile()).resolves.toMatchObject({ email: 'user@example.com' })
  })

  it('updates the display name', async () => {
    vi.mocked(apiClient.patch).mockResolvedValue({
      data: { id: userId, email: 'user@example.com', display_name: '张三', email_verified: false },
    })
    await expect(updateProfile('张三')).resolves.toMatchObject({ display_name: '张三' })
    expect(apiClient.patch).toHaveBeenCalledWith('/account/profile', { display_name: '张三' })
  })

  it('changes the password', async () => {
    vi.mocked(apiClient.post).mockResolvedValue({ data: undefined })
    await changePassword('old-password-x', 'a much stronger passphrase')
    expect(apiClient.post).toHaveBeenCalledWith('/account/password', {
      current_password: 'old-password-x',
      new_password: 'a much stronger passphrase',
    })
  })

  it('requests an email verification token', async () => {
    vi.mocked(apiClient.post).mockResolvedValue({ data: { token: 'verify-token' } })
    await expect(requestEmailVerification()).resolves.toBe('verify-token')
  })

  it('lists sessions and marks the current one', async () => {
    vi.mocked(apiClient.get).mockResolvedValue({
      data: [{
        id: '30000000-0000-4000-8000-000000000030',
        created_at: '2026-07-17T08:00:00Z',
        expires_at: '2026-07-24T08:00:00Z',
        revoked: false,
        active: true,
        current: true,
        user_agent: 'Mozilla/5.0',
      }],
    })
    await expect(listSessions()).resolves.toMatchObject([{ current: true }])
  })

  it('revokes all other sessions', async () => {
    vi.mocked(apiClient.delete).mockResolvedValue({ data: undefined })
    await revokeOtherSessions()
    expect(apiClient.delete).toHaveBeenCalledWith('/account/sessions')
  })
})
