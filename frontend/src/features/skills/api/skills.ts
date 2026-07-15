import { apiClient } from '../../../api/client'
import { tenantRequestConfig } from '../../../api/tenant'

export type SkillStatus = 'draft' | 'published' | 'archived' | 'deleted'
export type SkillReviewStatus = 'approved' | 'blocked'
export type SkillFindingSeverity = 'info' | 'warning' | 'blocker'

export interface Skill {
  id: string
  tenant_id: string
  name: string
  description: string
  status: SkillStatus
  latest_version: number
  published_version: number | null
  source: string
}

export interface SkillSecurityFinding {
  severity: SkillFindingSeverity
  category: string
  code: string
  message: string
  path: string | null
}

export interface SkillVersion {
  version: number
  description: string
  digest: string
  files: string[]
  review_status: SkillReviewStatus
  security_findings: SkillSecurityFinding[]
  created_at: string
  reviewed_at: string
  published_at: string | null
}

export interface SkillVersionDiff {
  from_version: number
  to_version: number
  added: string[]
  removed: string[]
  changed: string[]
}

export interface SkillUsageItem {
  employee_id: string
  employee_name: string
  relation: 'employee_draft' | 'employee_version'
  version: number | null
}

export interface SkillUsage {
  items: SkillUsageItem[]
}

function bundleBody(file: File): FormData {
  const body = new FormData()
  body.append('bundle', file)
  return body
}

export async function listSkills(tenantId: string): Promise<Skill[]> {
  return (await apiClient.get<Skill[]>('/skills', tenantRequestConfig(tenantId))).data
}

export async function getSkill(tenantId: string, skillId: string): Promise<Skill> {
  return (await apiClient.get<Skill>(`/skills/${skillId}`, tenantRequestConfig(tenantId))).data
}

export async function createSkill(tenantId: string, file: File): Promise<Skill> {
  return (await apiClient.post<Skill>('/skills', bundleBody(file), {
    ...tenantRequestConfig(tenantId),
  })).data
}

export async function addSkillVersion(
  tenantId: string,
  skillId: string,
  file: File,
): Promise<SkillVersion> {
  return (await apiClient.post<SkillVersion>(`/skills/${skillId}/versions`, bundleBody(file), {
    ...tenantRequestConfig(tenantId),
  })).data
}

export async function listSkillVersions(tenantId: string, skillId: string): Promise<SkillVersion[]> {
  return (await apiClient.get<SkillVersion[]>(`/skills/${skillId}/versions`, {
    ...tenantRequestConfig(tenantId),
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
    tenantRequestConfig(tenantId),
  )).data
}

export async function offlineSkill(tenantId: string, skillId: string): Promise<Skill> {
  return (await apiClient.post<Skill>(
    `/skills/${skillId}/offline`,
    undefined,
    tenantRequestConfig(tenantId),
  )).data
}

export async function deleteSkill(tenantId: string, skillId: string): Promise<void> {
  await apiClient.delete(`/skills/${skillId}`, tenantRequestConfig(tenantId))
}

export async function getSkillVersionDiff(
  tenantId: string,
  skillId: string,
  fromVersion: number,
  toVersion: number,
): Promise<SkillVersionDiff> {
  return (await apiClient.get<SkillVersionDiff>(
    `/skills/${skillId}/versions/${fromVersion}/diff/${toVersion}`,
    tenantRequestConfig(tenantId),
  )).data
}

export async function getSkillUsage(tenantId: string, skillId: string): Promise<SkillUsage> {
  return (await apiClient.get<SkillUsage>(
    `/skills/${skillId}/usage`,
    tenantRequestConfig(tenantId),
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
    { ...tenantRequestConfig(tenantId), responseType: 'text' },
  )).data
}
