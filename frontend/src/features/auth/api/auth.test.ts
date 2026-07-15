import { beforeEach, describe, expect, it, vi } from 'vitest'

import { apiClient } from '../../../api/client'
import { getCurrentUser } from './auth'

describe('authentication response boundary', () => {
  beforeEach(() => {
    vi.restoreAllMocks()
  })

  it('rejects a successful response that is not a current-user document', async () => {
    vi.spyOn(apiClient, 'get').mockResolvedValue({
      data: '<!doctype html><html></html>',
    })

    await expect(getCurrentUser()).rejects.toMatchObject({ name: 'ZodError' })
  })
})
