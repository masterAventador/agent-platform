import { describe, expect, it } from 'vitest'

import {
  isTenantMutationFor,
  tenantMutationKey,
  tenantRequestConfig,
} from './tenant'


describe('tenant API scope', () => {
  const tenantId = '00000000-0000-4000-8000-000000000010'

  it('builds the canonical tenant request header', () => {
    expect(tenantRequestConfig(tenantId)).toEqual({
      headers: { 'X-Tenant-ID': tenantId },
    })
  })

  it('builds stable mutation keys and identifies only the requested tenant', () => {
    const key = tenantMutationKey(tenantId, 'employees', 'update', 'employee-1')

    expect(key).toEqual([
      'tenant-mutation',
      tenantId,
      'employees',
      'update',
      'employee-1',
    ])
    expect(isTenantMutationFor(key, tenantId)).toBe(true)
    expect(isTenantMutationFor(key, '00000000-0000-4000-8000-000000000020')).toBe(false)
    expect(isTenantMutationFor(['employees', tenantId], tenantId)).toBe(false)
  })
})
