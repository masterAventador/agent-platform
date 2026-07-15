import { z } from 'zod'

import type { PlatformAdapter } from '../../platform'

const rememberedLoginSchema = z.object({
  email: z.email(),
  password: z.string().min(1).max(1_024),
}).strict()

export type RememberedLogin = z.infer<typeof rememberedLoginSchema>

function supportsRememberedLogin(platform: PlatformAdapter): boolean {
  return platform.capabilities().rememberedLogin
}

export async function loadRememberedLogin(
  platform: PlatformAdapter,
): Promise<RememberedLogin | null> {
  if (!supportsRememberedLogin(platform)) return null
  const stored = await platform.rememberedLogin.get()
  if (stored === null) return null
  try {
    return rememberedLoginSchema.parse(JSON.parse(stored))
  } catch {
    await platform.rememberedLogin.delete()
    return null
  }
}

export async function saveRememberedLogin(
  platform: PlatformAdapter,
  login: RememberedLogin,
): Promise<void> {
  if (!supportsRememberedLogin(platform)) return
  const validated = rememberedLoginSchema.parse(login)
  await platform.rememberedLogin.set(JSON.stringify(validated))
}

export async function clearRememberedLogin(platform: PlatformAdapter): Promise<void> {
  if (!supportsRememberedLogin(platform)) return
  await platform.rememberedLogin.delete()
}
