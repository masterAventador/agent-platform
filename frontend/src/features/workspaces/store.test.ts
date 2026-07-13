import { beforeEach, describe, expect, it } from 'vitest'

import type { Workspace } from './types'
import {
  ACTIVE_WORKSPACE_STORAGE_KEY,
  createWorkspaceStore,
} from './store'


const owner: Workspace = {
  id: '00000000-0000-4000-8000-000000000010',
  name: 'Owner workspace',
  slug: 'owner-workspace',
  role: 'owner',
  permissions: [],
}
const member: Workspace = {
  id: '00000000-0000-4000-8000-000000000020',
  name: 'Member workspace',
  slug: 'member-workspace',
  role: 'member',
  permissions: [],
}

describe('active workspace store', () => {
  beforeEach(() => sessionStorage.clear())

  it('仅在持久值仍属于当前用户时恢复', () => {
    sessionStorage.setItem(ACTIVE_WORKSPACE_STORAGE_KEY, JSON.stringify({
      user_id: 'user-1',
      workspace_id: member.id,
    }))
    const store = createWorkspaceStore(() => sessionStorage)

    store.getState().reconcile({ id: 'user-1', workspaces: [owner, member] })

    expect(store.getState().activeWorkspaceId).toBe(member.id)
    expect(store.getState().reconciledUserId).toBe('user-1')
  })

  it('stale workspace 回退到当前 membership 第一项并覆盖持久值', () => {
    sessionStorage.setItem(ACTIVE_WORKSPACE_STORAGE_KEY, JSON.stringify({
      user_id: 'user-1',
      workspace_id: '00000000-0000-4000-8000-000000000099',
    }))
    const store = createWorkspaceStore(() => sessionStorage)

    store.getState().reconcile({ id: 'user-1', workspaces: [owner, member] })

    expect(store.getState().activeWorkspaceId).toBe(owner.id)
    expect(JSON.parse(sessionStorage.getItem(ACTIVE_WORKSPACE_STORAGE_KEY)!)).toEqual({
      user_id: 'user-1',
      workspace_id: owner.id,
    })
  })

  it('切换账号必须重新校验而不能沿用另一用户的 workspace', () => {
    const store = createWorkspaceStore(() => sessionStorage)
    store.getState().reconcile({ id: 'user-1', workspaces: [owner, member] })
    expect(store.getState().select(member.id, [owner, member])).toBe(true)

    store.getState().reconcile({ id: 'user-2', workspaces: [owner] })

    expect(store.getState().activeWorkspaceId).toBe(owner.id)
    expect(JSON.parse(sessionStorage.getItem(ACTIVE_WORKSPACE_STORAGE_KEY)!)).toEqual({
      user_id: 'user-2',
      workspace_id: owner.id,
    })
  })

  it('拒绝选择 membership 之外的 workspace', () => {
    const store = createWorkspaceStore(() => sessionStorage)
    store.getState().reconcile({ id: 'user-1', workspaces: [owner] })

    expect(store.getState().select(member.id, [owner])).toBe(false)
    expect(store.getState().activeWorkspaceId).toBe(owner.id)
  })

  it('0 workspace 进入已校验空态，logout 清理内存和 sessionStorage', () => {
    const store = createWorkspaceStore(() => sessionStorage)
    store.getState().reconcile({ id: 'user-1', workspaces: [] })
    expect(store.getState()).toMatchObject({
      reconciledUserId: 'user-1',
      activeWorkspaceId: undefined,
    })
    expect(sessionStorage.getItem(ACTIVE_WORKSPACE_STORAGE_KEY)).toBeNull()

    store.getState().reconcile({ id: 'user-1', workspaces: [owner] })
    store.getState().clear()
    expect(store.getState()).toMatchObject({
      reconciledUserId: undefined,
      activeWorkspaceId: undefined,
    })
    expect(sessionStorage.getItem(ACTIVE_WORKSPACE_STORAGE_KEY)).toBeNull()
  })
})
