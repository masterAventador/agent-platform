import { apiClient } from '../../../api/client'

export type SkillStatus = 'draft' | 'published'

export interface Skill {
  id: string
  tenant_id: string
  name: string
  description: string
  status: SkillStatus
  latest_version: number
  published_version: number | null
}

export interface SkillVersion {
  version: number
  description: string
  digest: string
  files: string[]
  created_at: string
  published_at: string | null
}

const tenantHeaders = (tenantId: string) => ({ 'X-Tenant-ID': tenantId })

function bundleBody(file: File): FormData {
  const body = new FormData()
  body.append('bundle', file)
  return body
}

export async function listSkills(tenantId: string): Promise<Skill[]> {
  return (await apiClient.get<Skill[]>('/skills', { headers: tenantHeaders(tenantId) })).data
}

export async function getSkill(tenantId: string, skillId: string): Promise<Skill> {
  return (await apiClient.get<Skill>(`/skills/${skillId}`, { headers: tenantHeaders(tenantId) })).data
}

export async function createSkill(tenantId: string, file: File): Promise<Skill> {
  return (await apiClient.post<Skill>('/skills', bundleBody(file), {
    headers: tenantHeaders(tenantId),
  })).data
}

export async function addSkillVersion(
  tenantId: string,
  skillId: string,
  file: File,
): Promise<SkillVersion> {
  return (await apiClient.post<SkillVersion>(`/skills/${skillId}/versions`, bundleBody(file), {
    headers: tenantHeaders(tenantId),
  })).data
}

export async function listSkillVersions(tenantId: string, skillId: string): Promise<SkillVersion[]> {
  return (await apiClient.get<SkillVersion[]>(`/skills/${skillId}/versions`, {
    headers: tenantHeaders(tenantId),
  })).data
}

export async function publishSkillVersion(
  tenantId: string,
  skillId: string,
  version: number,
): Promise<Skill> {
  return (await apiClient.post<Skill>(
    `/skills/${skillId}/versions/${version}/publish`,
    undefined,
    { headers: tenantHeaders(tenantId) },
  )).data
}

export async function readSkillFile(
  tenantId: string,
  skillId: string,
  version: number,
  path: string,
): Promise<string> {
  const encodedPath = encodeURIComponent(path).replaceAll('%2F', '/')
  return (await apiClient.get<string>(
    `/skills/${skillId}/versions/${version}/files/${encodedPath}`,
    { headers: tenantHeaders(tenantId), responseType: 'text' },
  )).data
}
