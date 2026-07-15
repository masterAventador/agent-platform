import { describe, expect, it } from 'vitest'

import { parseCapabilityRegistry, resolveCapabilityAccess } from './registry'

const descriptor = {
  capabilityId: 'social-operations',
  frontendEntries: ['social.routes.v1'],
  permissions: ['social.read', 'social.manage', 'social.execute'],
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

  it.each([
    ['permissions', { ...capability, permissions: ['social.read', 'social.read', 'social.manage'] }],
    [
      'frontend_entries',
      { ...capability, frontend_entries: ['social.routes.v1', 'social.routes.v1'] },
    ],
  ])('拒绝包含重复 %s 的后端能力声明', (_field, repeatedCapability) => {
    expect(() => parseCapabilityRegistry({
      schema_version: '1.0',
      capabilities: [repeatedCapability],
    })).toThrow()
  })

  it('部署、租户、用户权限和 frontend entry 任一缺失都拒绝装配', () => {
    const userPermissions = new Set(capability.permissions)

    expect(resolveCapabilityAccess(undefined, undefined, userPermissions)).toBe('not-installed')
    expect(resolveCapabilityAccess(
      { ...capability, deployment_installed: false },
      descriptor,
      userPermissions,
    )).toBe('not-installed')
    expect(resolveCapabilityAccess(
      { ...capability, tenant_entitled: false },
      descriptor,
      userPermissions,
    )).toBe('not-entitled')
    expect(resolveCapabilityAccess(capability, descriptor, new Set(['runs.execute'])))
      .toBe('forbidden')
    expect(resolveCapabilityAccess(
      { ...capability, frontend_entries: ['social.other.v1'] },
      descriptor,
      userPermissions,
    )).toBe('incompatible')
    expect(resolveCapabilityAccess(capability, descriptor, userPermissions)).toBe('allowed')
  })

  it('防御性拒绝用重复项替代缺失项的精确声明比较', () => {
    const repeatedPermissions = ['social.read', 'social.read', 'social.manage']
    const repeatedFrontendEntries = ['social.routes.v1', 'social.routes.v1']
    const multiEntryDescriptor = {
      ...descriptor,
      frontendEntries: ['social.routes.v1', 'social.settings.v1'],
    }

    expect(resolveCapabilityAccess(
      { ...capability, permissions: repeatedPermissions },
      descriptor,
      new Set(capability.permissions),
    )).toBe('incompatible')
    expect(resolveCapabilityAccess(
      { ...capability, permissions: repeatedPermissions },
      { ...descriptor, permissions: repeatedPermissions },
      new Set(capability.permissions),
    )).toBe('incompatible')
    expect(resolveCapabilityAccess(
      { ...capability, frontend_entries: repeatedFrontendEntries },
      multiEntryDescriptor,
      new Set(capability.permissions),
    )).toBe('incompatible')
  })
})
