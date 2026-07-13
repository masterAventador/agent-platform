import { apiClient } from '../../../api/client'

export interface CurrentUser {
  id: string
  email: string
  email_verified: boolean
  workspaces: Workspace[]
}

export interface Workspace {
  id: string
  name: string
  slug: string
  role: 'owner' | 'admin' | 'member'
}

export interface Credentials {
  email: string
  password: string
}

export async function register(credentials: Credentials): Promise<CurrentUser> {
  const response = await apiClient.post<CurrentUser>('/auth/register', credentials)
  return response.data
}

export async function login(credentials: Credentials): Promise<CurrentUser> {
  const response = await apiClient.post<CurrentUser>('/auth/login', credentials)
  return response.data
}

export async function getCurrentUser(): Promise<CurrentUser> {
  const response = await apiClient.get<CurrentUser>('/auth/me')
  return response.data
}

export async function logout(): Promise<void> {
  await apiClient.post('/auth/logout')
}
