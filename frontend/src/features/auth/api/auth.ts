import { z } from 'zod'

import { apiClient } from '../../../api/client'
import type { Workspace } from '../../workspaces/types'

export interface CurrentUser {
  id: string
  email: string
  email_verified: boolean
  workspaces: Workspace[]
}

export interface Credentials {
  email: string
  password: string
}

const currentUserSchema: z.ZodType<CurrentUser> = z.object({
  id: z.string().min(1),
  email: z.email(),
  email_verified: z.boolean(),
  workspaces: z.array(z.object({
    id: z.string().min(1),
    name: z.string().min(1),
    slug: z.string().min(1),
    role: z.enum(['owner', 'admin', 'member']),
    permissions: z.array(z.string()),
  })),
})

export async function register(credentials: Credentials): Promise<CurrentUser> {
  const response = await apiClient.post<CurrentUser>('/auth/register', credentials)
  return currentUserSchema.parse(response.data)
}

export async function login(credentials: Credentials): Promise<CurrentUser> {
  const response = await apiClient.post<CurrentUser>('/auth/login', credentials)
  return currentUserSchema.parse(response.data)
}

export async function getCurrentUser(): Promise<CurrentUser> {
  const response = await apiClient.get<CurrentUser>('/auth/me')
  return currentUserSchema.parse(response.data)
}

export async function logout(): Promise<void> {
  await apiClient.post('/auth/logout')
}
