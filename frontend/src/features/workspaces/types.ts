export type WorkspaceRole = 'owner' | 'admin' | 'member'

export interface Workspace {
  id: string
  name: string
  slug: string
  role: WorkspaceRole
  permissions: string[]
}

export interface WorkspaceUser {
  id: string
  workspaces: Workspace[]
}
