export const TENANT_HEADER_NAME = 'X-Tenant-ID'
export const TENANT_MUTATION_KEY_PREFIX = 'tenant-mutation'

export function tenantRequestConfig(tenantId: string) {
  return {
    headers: { [TENANT_HEADER_NAME]: tenantId },
  }
}

export function tenantMutationKey<const Scope extends readonly unknown[]>(
  tenantId: string,
  ...scope: Scope
) {
  return [TENANT_MUTATION_KEY_PREFIX, tenantId, ...scope] as const
}

export function isTenantMutationFor(
  mutationKey: readonly unknown[] | undefined,
  tenantId: string,
): boolean {
  return mutationKey?.[0] === TENANT_MUTATION_KEY_PREFIX
    && mutationKey[1] === tenantId
}
