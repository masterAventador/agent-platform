import { describe, expect, it } from 'vitest'

import { scheduledTaskKeys } from './queries'


const tenantA = '10000000-0000-4000-8000-00000000000a'
const tenantB = '10000000-0000-4000-8000-00000000000b'
const taskId = '20000000-0000-4000-8000-000000000020'

describe('scheduledTaskKeys', () => {
  // 架构约束：Query Key 必须由 Feature 统一工厂生成且带租户作用域，
  // 否则切换企业后会读到上一个租户的缓存。
  it('每个 Key 都以租户作用域开头', () => {
    expect(scheduledTaskKeys.all(tenantA)).toEqual(['scheduled-tasks', tenantA])
    expect(scheduledTaskKeys.list(tenantA, undefined, 0))
      .toEqual(['scheduled-tasks', tenantA, 'list', undefined, 0])
    expect(scheduledTaskKeys.detail(tenantA, taskId))
      .toEqual(['scheduled-tasks', tenantA, 'detail', taskId])
    expect(scheduledTaskKeys.executions(tenantA, taskId, 0))
      .toEqual(['scheduled-tasks', tenantA, 'executions', taskId, 0])
  })

  it('不同租户的同一资源产生不同的 Key，缓存不串用', () => {
    expect(scheduledTaskKeys.detail(tenantA, taskId))
      .not.toEqual(scheduledTaskKeys.detail(tenantB, taskId))
    expect(scheduledTaskKeys.executions(tenantA, taskId, 0))
      .not.toEqual(scheduledTaskKeys.executions(tenantB, taskId, 0))
  })

  // App.tsx 切换工作区时按 `queryKey.includes(previousWorkspaceId)` 清缓存，
  // 租户 ID 必须是 Key 的独立元素才会被清掉。
  it('租户 ID 是 Key 的独立元素，可被工作区切换逻辑精确清理', () => {
    expect(scheduledTaskKeys.detail(tenantA, taskId)).toContain(tenantA)
    expect(scheduledTaskKeys.all(tenantA)).toContain(tenantA)
  })

  it('列表 Key 区分员工筛选与分页', () => {
    const employeeId = '30000000-0000-4000-8000-000000000030'
    expect(scheduledTaskKeys.list(tenantA, employeeId, 0))
      .not.toEqual(scheduledTaskKeys.list(tenantA, undefined, 0))
    expect(scheduledTaskKeys.list(tenantA, employeeId, 50))
      .not.toEqual(scheduledTaskKeys.list(tenantA, employeeId, 0))
  })
})
