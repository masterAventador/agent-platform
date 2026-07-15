import { describe, expect, it } from 'vitest'

import type { FrontendCapabilityModule } from './types'
import { parseCapabilityRegistry, resolveCapabilityAccess } from './registry'

const module: FrontendCapabilityModule = {
  capabilityId: 'social-operations',
  frontendEntry: 'social.routes.v1',
  navigation: [{ label: '设备与平台账号', path: '/video/account' }],
  routes: [],
}

const capability = {
  capability_id: 'social-operations',
  deployment_installed: true,
  tenant_entitled: true,
  frontend_entries: ['social.routes.v1'],
  permissions: ['social.read', 'social.manage', 'social.execute'],
}

describe('capability registry', () => {
  it('严格解析后端裁剪后的 1.0 能力协议', () => {
    expect(parseCapabilityRegistry({
      schema_version: '1.0',
      capabilities: [capability],
    })).toEqual({
      schema_version: '1.0',
      capabilities: [capability],
    })

    expect(() => parseCapabilityRegistry({
      schema_version: '1.0',
      capabilities: [{ ...capability, permissions: [] }],
    })).toThrow()
    expect(() => parseCapabilityRegistry({
      schema_version: '2.0',
      capabilities: [capability],
    })).toThrow()
  })

  it('部署、租户、用户权限和 frontend entry 任一缺失都拒绝装配', () => {
    const userPermissions = new Set(capability.permissions)

    expect(resolveCapabilityAccess(undefined, undefined, userPermissions)).toBe('not-installed')
    expect(resolveCapabilityAccess(
      { ...capability, deployment_installed: false },
      module,
      userPermissions,
    )).toBe('not-installed')
    expect(resolveCapabilityAccess(
      { ...capability, tenant_entitled: false },
      module,
      userPermissions,
    )).toBe('not-entitled')
    expect(resolveCapabilityAccess(capability, module, new Set(['runs.execute'])))
      .toBe('forbidden')
    expect(resolveCapabilityAccess(
      { ...capability, frontend_entries: ['social.other.v1'] },
      module,
      userPermissions,
    )).toBe('incompatible')
    expect(resolveCapabilityAccess(capability, module, userPermissions)).toBe('allowed')
  })
})
