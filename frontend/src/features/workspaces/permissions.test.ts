import { describe, expect, it } from 'vitest'

import type { Workspace } from './types'
import {
  getWorkspaceCapabilities,
  hasWorkspacePermission,
  workspacePermissions,
} from './permissions'


function workspace(
  role: Workspace['role'],
  permissions: Workspace['permissions'],
): Workspace {
  return {
    id: `workspace-${role}`,
    name: `${role} workspace`,
    slug: `${role}-workspace`,
    role,
    permissions,
  }
}

describe('workspace capabilities', () => {
  it('derives every UI capability from stable backend permission codes', () => {
    const capabilities = getWorkspaceCapabilities(workspace('member', [
      workspacePermissions.workspaceManage,
      workspacePermissions.employeesManage,
      workspacePermissions.runsExecute,
      workspacePermissions.runsManage,
      workspacePermissions.knowledgeManage,
      workspacePermissions.skillsManage,
      workspacePermissions.toolsManage,
      workspacePermissions.operationsManage,
      workspacePermissions.modelsManage,
      workspacePermissions.modelsUsageRead,
    ]))

    expect(capabilities).toEqual({
      canManageWorkspace: true,
      canManageEmployees: true,
      canExecuteRuns: true,
      canManageRuns: true,
      canManageKnowledge: true,
      canManageSkills: true,
      canManageTools: true,
      canManageOperations: true,
      canManageModels: true,
      canReadModelsUsage: true,
    })
  })

  it('does not infer grants from an owner or admin role', () => {
    expect(getWorkspaceCapabilities(workspace('owner', []))).toEqual({
      canManageWorkspace: false,
      canManageEmployees: false,
      canExecuteRuns: false,
      canManageRuns: false,
      canManageKnowledge: false,
      canManageSkills: false,
      canManageTools: false,
      canManageOperations: false,
      canManageModels: false,
      canReadModelsUsage: false,
    })
    expect(getWorkspaceCapabilities(workspace('admin', [
      workspacePermissions.operationsManage,
    ]))).toMatchObject({
      canManageEmployees: false,
      canManageOperations: true,
    })
  })

  it('ignores duplicate and unknown permission codes without widening access', () => {
    const capabilities = getWorkspaceCapabilities(workspace('member', [
      workspacePermissions.runsExecute,
      workspacePermissions.runsExecute,
      'future.permission',
    ]))

    expect(capabilities.canExecuteRuns).toBe(true)
    expect(capabilities.canManageEmployees).toBe(false)
    expect(capabilities.canManageOperations).toBe(false)
    expect(capabilities.canManageModels).toBe(false)
    expect(capabilities.canReadModelsUsage).toBe(false)
  })

  it('fails closed instead of crashing when an old API response omits permissions', () => {
    const legacyWorkspace = {
      id: 'legacy-workspace',
      name: 'Legacy workspace',
      slug: 'legacy-workspace',
      role: 'owner',
    } as unknown as Workspace

    expect(getWorkspaceCapabilities(legacyWorkspace)).toEqual({
      canManageWorkspace: false,
      canManageEmployees: false,
      canExecuteRuns: false,
      canManageRuns: false,
      canManageKnowledge: false,
      canManageSkills: false,
      canManageTools: false,
      canManageOperations: false,
      canManageModels: false,
      canReadModelsUsage: false,
    })
    expect(hasWorkspacePermission(
      legacyWorkspace,
      workspacePermissions.workspaceManage,
    )).toBe(false)
  })
})
