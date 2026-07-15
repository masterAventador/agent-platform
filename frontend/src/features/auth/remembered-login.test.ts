import { describe, expect, it, vi } from 'vitest'

import type { PlatformAdapter } from '../../platform'
import {
  clearRememberedLogin,
  loadRememberedLogin,
  saveRememberedLogin,
} from './remembered-login'

function platformAdapter(options: {
  rememberedLogin?: boolean
  storedValue?: string | null
} = {}) {
  const rememberedLogin = {
    get: vi.fn().mockResolvedValue(options.storedValue ?? null),
    set: vi.fn().mockResolvedValue(undefined),
    delete: vi.fn().mockResolvedValue(undefined),
  }
  const adapter = {
    capabilities: () => ({ rememberedLogin: options.rememberedLogin ?? true }),
    rememberedLogin,
  } as unknown as PlatformAdapter
  return { adapter, rememberedLogin }
}

describe('remembered desktop login', () => {
  it('loads a valid login only through App-private storage', async () => {
    const { adapter, rememberedLogin } = platformAdapter({
      storedValue: JSON.stringify({ email: 'demo@example.com', password: 'secret' }),
    })

    await expect(loadRememberedLogin(adapter)).resolves.toEqual({
      email: 'demo@example.com',
      password: 'secret',
    })
    expect(rememberedLogin.get).toHaveBeenCalledOnce()
  })

  it('fails closed and deletes malformed App content', async () => {
    const { adapter, rememberedLogin } = platformAdapter({ storedValue: '{invalid-json' })

    await expect(loadRememberedLogin(adapter)).resolves.toBeNull()
    expect(rememberedLogin.delete).toHaveBeenCalledOnce()
  })

  it('stores and clears login data without falling back to browser storage', async () => {
    const { adapter, rememberedLogin } = platformAdapter()

    await saveRememberedLogin(adapter, {
      email: 'demo@example.com',
      password: 'secret',
    })
    await clearRememberedLogin(adapter)

    expect(rememberedLogin.set).toHaveBeenCalledWith(
      JSON.stringify({ email: 'demo@example.com', password: 'secret' }),
    )
    expect(rememberedLogin.delete).toHaveBeenCalledOnce()

    const web = platformAdapter({ rememberedLogin: false })
    await expect(loadRememberedLogin(web.adapter)).resolves.toBeNull()
    await saveRememberedLogin(web.adapter, { email: 'demo@example.com', password: 'secret' })
    await clearRememberedLogin(web.adapter)
    expect(web.rememberedLogin.get).not.toHaveBeenCalled()
    expect(web.rememberedLogin.set).not.toHaveBeenCalled()
    expect(web.rememberedLogin.delete).not.toHaveBeenCalled()
  })
})
