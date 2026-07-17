import { z } from 'zod'

import { apiClient } from '../../../api/client'

const uuidSchema = z.uuid()
const dateTimeSchema = z.iso.datetime({ offset: true })

const profileSchema = z.object({
  id: uuidSchema,
  email: z.email(),
  display_name: z.string().nullable(),
  email_verified: z.boolean(),
}).strict()

const tokenSchema = z.object({ token: z.string().nullable() }).strict()

const sessionSchema = z.object({
  id: uuidSchema,
  created_at: dateTimeSchema,
  expires_at: dateTimeSchema,
  revoked: z.boolean(),
  active: z.boolean(),
  current: z.boolean(),
  user_agent: z.string().nullable(),
}).strict()

const sessionListSchema = z.array(sessionSchema)

export type Profile = z.infer<typeof profileSchema>
export type SessionInfo = z.infer<typeof sessionSchema>

export async function getProfile(): Promise<Profile> {
  const response = await apiClient.get('/account/profile')
  return profileSchema.parse(response.data)
}

export async function updateProfile(displayName: string | null): Promise<Profile> {
  const response = await apiClient.patch('/account/profile', { display_name: displayName })
  return profileSchema.parse(response.data)
}

export async function changePassword(
  currentPassword: string,
  newPassword: string,
): Promise<void> {
  await apiClient.post('/account/password', {
    current_password: currentPassword,
    new_password: newPassword,
  })
}

export async function requestEmailVerification(): Promise<string | null> {
  const response = await apiClient.post('/account/email-verification/request')
  return tokenSchema.parse(response.data).token
}

export async function confirmEmailVerification(token: string): Promise<void> {
  await apiClient.post('/account/email-verification/confirm', { token })
}

export async function requestPasswordReset(email: string): Promise<void> {
  await apiClient.post('/account/password-reset/request', { email })
}

export async function confirmPasswordReset(token: string, newPassword: string): Promise<void> {
  await apiClient.post('/account/password-reset/confirm', {
    token,
    new_password: newPassword,
  })
}

export async function listSessions(): Promise<SessionInfo[]> {
  const response = await apiClient.get('/account/sessions')
  return sessionListSchema.parse(response.data)
}

export async function revokeSession(sessionId: string): Promise<void> {
  await apiClient.delete(`/account/sessions/${sessionId}`)
}

export async function revokeOtherSessions(): Promise<void> {
  await apiClient.delete('/account/sessions')
}
