import type { Workspace } from './types'


export const workspacePermissions = {
  workspaceManage: 'workspace.manage',
  employeesManage: 'employees.manage',
  runsExecute: 'runs.execute',
  runsManage: 'runs.manage',
  knowledgeManage: 'knowledge.manage',
  skillsManage: 'skills.manage',
  toolsManage: 'tools.manage',
  operationsManage: 'operations.manage',
  modelsManage: 'models.manage',
  modelsUsageRead: 'models.usage.read',
} as const

export type WorkspacePermission = typeof workspacePermissions[keyof typeof workspacePermissions]

export interface WorkspaceCapabilities {
  canManageWorkspace: boolean
  canManageEmployees: boolean
  canExecuteRuns: boolean
  canManageRuns: boolean
  canManageKnowledge: boolean
  canManageSkills: boolean
  canManageTools: boolean
  canManageOperations: boolean
  canManageModels: boolean
  canReadModelsUsage: boolean
}

export function hasWorkspacePermission(
  workspace: Workspace,
  permission: WorkspacePermission,
): boolean {
  return Array.isArray(workspace.permissions) && workspace.permissions.includes(permission)
}

export function getWorkspaceCapabilities(workspace: Workspace): WorkspaceCapabilities {
  const granted = new Set(Array.isArray(workspace.permissions) ? workspace.permissions : [])

  return {
    canManageWorkspace: granted.has(workspacePermissions.workspaceManage),
    canManageEmployees: granted.has(workspacePermissions.employeesManage),
    canExecuteRuns: granted.has(workspacePermissions.runsExecute),
    canManageRuns: granted.has(workspacePermissions.runsManage),
    canManageKnowledge: granted.has(workspacePermissions.knowledgeManage),
    canManageSkills: granted.has(workspacePermissions.skillsManage),
    canManageTools: granted.has(workspacePermissions.toolsManage),
    canManageOperations: granted.has(workspacePermissions.operationsManage),
    canManageModels: granted.has(workspacePermissions.modelsManage),
    canReadModelsUsage: granted.has(workspacePermissions.modelsUsageRead),
  }
}
