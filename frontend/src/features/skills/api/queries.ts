import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'

import { useActiveWorkspaceId } from '../../workspaces/store'
import {
  addSkillVersion,
  createSkill,
  getSkill,
  listSkills,
  listSkillVersions,
  publishSkillVersion,
  readSkillFile,
} from './skills'

const skillKeys = {
  all: (tenantId: string) => ['skills', tenantId] as const,
  detail: (tenantId: string, skillId: string) => ['skills', tenantId, skillId] as const,
  versions: (tenantId: string, skillId: string) => ['skills', tenantId, skillId, 'versions'] as const,
  file: (tenantId: string, skillId: string, version: number, path: string) =>
    ['skills', tenantId, skillId, 'versions', version, 'files', path] as const,
}

export function useSkills() {
  const tenantId = useActiveWorkspaceId()
  return useQuery({
    queryKey: skillKeys.all(tenantId ?? ''),
    queryFn: () => listSkills(tenantId!),
    enabled: Boolean(tenantId),
  })
}

export function usePublishedSkills() {
  const skills = useSkills()
  return { ...skills, data: skills.data?.filter((skill) => skill.status === 'published') }
}

export function useSkill(skillId: string | undefined) {
  const tenantId = useActiveWorkspaceId()
  return useQuery({
    queryKey: skillKeys.detail(tenantId ?? '', skillId ?? ''),
    queryFn: () => getSkill(tenantId!, skillId!),
    enabled: Boolean(tenantId && skillId),
  })
}

export function useSkillVersions(skillId: string | undefined) {
  const tenantId = useActiveWorkspaceId()
  return useQuery({
    queryKey: skillKeys.versions(tenantId ?? '', skillId ?? ''),
    queryFn: () => listSkillVersions(tenantId!, skillId!),
    enabled: Boolean(tenantId && skillId),
  })
}

export function useSkillFile(skillId: string, version: number | undefined, path: string | undefined) {
  const tenantId = useActiveWorkspaceId()
  return useQuery({
    queryKey: skillKeys.file(tenantId ?? '', skillId, version ?? 0, path ?? ''),
    queryFn: () => readSkillFile(tenantId!, skillId, version!, path!),
    enabled: Boolean(tenantId && skillId && version && path),
  })
}

export function useCreateSkill() {
  const tenantId = useActiveWorkspaceId()
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: (file: File) => createSkill(tenantId!, file),
    onSuccess: async () => queryClient.invalidateQueries({ queryKey: skillKeys.all(tenantId!) }),
  })
}

export function useAddSkillVersion(skillId: string) {
  const tenantId = useActiveWorkspaceId()
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: (file: File) => addSkillVersion(tenantId!, skillId, file),
    onSuccess: async () => {
      await Promise.all([
        queryClient.invalidateQueries({ queryKey: skillKeys.all(tenantId!) }),
        queryClient.invalidateQueries({ queryKey: skillKeys.detail(tenantId!, skillId) }),
        queryClient.invalidateQueries({ queryKey: skillKeys.versions(tenantId!, skillId) }),
      ])
    },
  })
}

export function usePublishSkillVersion(skillId: string) {
  const tenantId = useActiveWorkspaceId()
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: (version: number) => publishSkillVersion(tenantId!, skillId, version),
    onSuccess: async (skill) => {
      queryClient.setQueryData(skillKeys.detail(tenantId!, skillId), skill)
      await Promise.all([
        queryClient.invalidateQueries({ queryKey: skillKeys.all(tenantId!) }),
        queryClient.invalidateQueries({ queryKey: skillKeys.versions(tenantId!, skillId) }),
      ])
    },
  })
}
