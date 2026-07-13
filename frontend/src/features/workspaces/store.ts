import { useEffect } from 'react'
import { create, type StoreApi, type UseBoundStore } from 'zustand'

import type { Workspace, WorkspaceUser } from './types'


export const ACTIVE_WORKSPACE_STORAGE_KEY = 'agent-platform.active-workspace'

type StorageFactory = () => Storage

interface StoredWorkspaceSelection {
  user_id: string
  workspace_id: string
}

interface WorkspaceState {
  activeWorkspaceId: string | undefined
  reconciledUserId: string | undefined
  reconcile: (user: WorkspaceUser) => void
  select: (workspaceId: string, workspaces: Workspace[]) => boolean
  clear: () => void
}

function readStoredSelection(storage: Storage): StoredWorkspaceSelection | undefined {
  try {
    const parsed = JSON.parse(
      storage.getItem(ACTIVE_WORKSPACE_STORAGE_KEY) ?? 'null',
    ) as Partial<StoredWorkspaceSelection> | null
    if (typeof parsed?.user_id !== 'string' || typeof parsed.workspace_id !== 'string') {
      return undefined
    }
    return { user_id: parsed.user_id, workspace_id: parsed.workspace_id }
  } catch {
    return undefined
  }
}

function persistSelection(
  storage: Storage,
  userId: string,
  workspaceId: string | undefined,
) {
  if (workspaceId === undefined) {
    storage.removeItem(ACTIVE_WORKSPACE_STORAGE_KEY)
    return
  }
  storage.setItem(ACTIVE_WORKSPACE_STORAGE_KEY, JSON.stringify({
    user_id: userId,
    workspace_id: workspaceId,
  }))
}

export function createWorkspaceStore(
  storageFactory: StorageFactory,
): UseBoundStore<StoreApi<WorkspaceState>> {
  return create<WorkspaceState>((set, get) => ({
    activeWorkspaceId: undefined,
    reconciledUserId: undefined,
    reconcile: (user) => {
      const storage = storageFactory()
      const current = get()
      const persisted = readStoredSelection(storage)
      const availableIds = new Set(user.workspaces.map((workspace) => workspace.id))
      const currentId = current.reconciledUserId === user.id
        && current.activeWorkspaceId !== undefined
        && availableIds.has(current.activeWorkspaceId)
        ? current.activeWorkspaceId
        : undefined
      const persistedId = persisted?.user_id === user.id
        && availableIds.has(persisted.workspace_id)
        ? persisted.workspace_id
        : undefined
      const activeWorkspaceId = currentId ?? persistedId ?? user.workspaces.at(0)?.id
      set({ activeWorkspaceId, reconciledUserId: user.id })
      persistSelection(storage, user.id, activeWorkspaceId)
    },
    select: (workspaceId, workspaces) => {
      if (!workspaces.some((workspace) => workspace.id === workspaceId)) return false
      const userId = get().reconciledUserId
      if (userId === undefined) return false
      set({ activeWorkspaceId: workspaceId })
      persistSelection(storageFactory(), userId, workspaceId)
      return true
    },
    clear: () => {
      set({ activeWorkspaceId: undefined, reconciledUserId: undefined })
      storageFactory().removeItem(ACTIVE_WORKSPACE_STORAGE_KEY)
    },
  }))
}

export const useWorkspaceStore = createWorkspaceStore(() => window.sessionStorage)

export function useActiveWorkspaceId(): string | undefined {
  return useWorkspaceStore((state) => state.activeWorkspaceId)
}

export function useWorkspaceSelection(user: WorkspaceUser) {
  const activeWorkspaceId = useActiveWorkspaceId()
  const reconciledUserId = useWorkspaceStore((state) => state.reconciledUserId)
  const reconcile = useWorkspaceStore((state) => state.reconcile)
  const select = useWorkspaceStore((state) => state.select)

  useEffect(() => reconcile(user), [reconcile, user])

  const activeWorkspace = reconciledUserId === user.id
    ? user.workspaces.find((workspace) => workspace.id === activeWorkspaceId)
    : undefined
  const isReconciled = reconciledUserId === user.id
    && (user.workspaces.length === 0
      ? activeWorkspaceId === undefined
      : activeWorkspace !== undefined)

  return {
    activeWorkspace,
    isReconciled,
    select: (workspaceId: string) => select(workspaceId, user.workspaces),
  }
}
