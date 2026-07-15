import { describe, expect, it, vi } from 'vitest'

import type { CapabilityRegistryEntry } from './registry'
import type { FrontendCapabilityModule } from './types'
import {
  loadAuthorizedFrontendCapabilityModules,
  type FrontendCapabilityDescriptor,
} from './modules'

const permissions = ['social.read', 'social.manage', 'social.execute']
const Page = () => null
const capability: CapabilityRegistryEntry = {
  capability_id: 'social-operations',
  deployment_installed: true,
  tenant_entitled: true,
  frontend_entries: ['social.routes.v1'],
  permissions,
}
const module: FrontendCapabilityModule = {
  capabilityId: 'social-operations',
  frontendEntry: 'social.routes.v1',
  navigation: [{ label: '设备与平台账号', path: '/video/account' }],
  routes: [
    { path: '/video/account', Page },
    { path: '/tiktok/account', Page },
  ],
}

function descriptor(load: () => Promise<FrontendCapabilityModule>): FrontendCapabilityDescriptor {
  return {
    capabilityId: 'social-operations',
    frontendEntries: ['social.routes.v1'],
    permissions,
    navigation: [{ label: '设备与平台账号', path: '/video/account' }],
    routePaths: ['/video/account', '/tiktok/account'],
    load,
  }
}

describe('authorized frontend capability module loading', () => {
  it.each([
    ['deployment 未安装', { ...capability, deployment_installed: false }, new Set(permissions)],
    ['tenant 未授权', { ...capability, tenant_entitled: false }, new Set(permissions)],
    ['用户无 social 权限', capability, new Set(['runs.execute'])],
    [
      'frontend_entries 未知',
      { ...capability, frontend_entries: ['social.unknown.v1'] },
      new Set(permissions),
    ],
    [
      'frontend_entries 混入恶意声明',
      { ...capability, frontend_entries: ['social.routes.v1', 'social.evil.v1'] },
      new Set(permissions),
    ],
  ])('%s 时不执行模块 loader', async (_name, registryCapability, userPermissions) => {
    const load = vi.fn().mockResolvedValue(module)

    const resolved = await loadAuthorizedFrontendCapabilityModules(
      [registryCapability],
      userPermissions,
      [descriptor(load)],
    )

    expect(load).not.toHaveBeenCalled()
    expect(resolved[0]?.module).toBeUndefined()
  })

  it('registry 畸形时在解析阶段失败且不执行模块 loader', async () => {
    const load = vi.fn().mockResolvedValue(module)

    await expect(loadAuthorizedFrontendCapabilityModules(
      [{ ...capability, permissions: [] }],
      new Set(permissions),
      [descriptor(load)],
    )).rejects.toThrow()

    expect(load).not.toHaveBeenCalled()
  })

  it('仅三层满足且 frontend_entries 与公开元数据完全一致时加载一次', async () => {
    const load = vi.fn().mockResolvedValue(module)

    const resolved = await loadAuthorizedFrontendCapabilityModules(
      [capability],
      new Set(permissions),
      [descriptor(load)],
    )

    expect(load).toHaveBeenCalledOnce()
    expect(resolved[0]).toMatchObject({ access: 'allowed', module })
  })
})
