export type WorkspaceRole = 'owner' | 'admin' | 'member'

export interface Workspace {
  id: string
  name: string
  slug: string
  role: WorkspaceRole
}

export interface WorkspaceUser {
  id: string
  workspaces: Workspace[]
}
