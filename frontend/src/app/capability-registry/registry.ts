import { z } from 'zod'

import type { FrontendCapabilityModule } from './types'

const resourceDeclaration = z.string().regex(/^[a-z][a-z0-9_]*(?:\.[a-z][a-z0-9_]*)+$/)

const capabilityRegistryEntrySchema = z.object({
  capability_id: z.string().regex(/^[a-z0-9]+(?:-[a-z0-9]+)*$/),
  deployment_installed: z.boolean(),
  tenant_entitled: z.boolean(),
  frontend_entries: z.array(resourceDeclaration).min(1),
  permissions: z.array(resourceDeclaration).min(1),
}).strict()

const capabilityRegistrySchema = z.object({
  schema_version: z.literal('1.0'),
  capabilities: z.array(capabilityRegistryEntrySchema),
}).strict().superRefine((registry, context) => {
  const capabilityIds = registry.capabilities.map((capability) => capability.capability_id)
  if (new Set(capabilityIds).size !== capabilityIds.length) {
    context.addIssue({ code: 'custom', message: 'duplicate capability id' })
  }
})

export type CapabilityRegistryEntry = z.infer<typeof capabilityRegistryEntrySchema>
export type CapabilityRegistry = z.infer<typeof capabilityRegistrySchema>
export type CapabilityAccess =
  | 'allowed'
  | 'forbidden'
  | 'incompatible'
  | 'not-entitled'
  | 'not-installed'

export function parseCapabilityRegistry(input: unknown): CapabilityRegistry {
  return capabilityRegistrySchema.parse(input)
}

export function resolveCapabilityAccess(
  capability: CapabilityRegistryEntry | undefined,
  module: FrontendCapabilityModule | undefined,
  userPermissions: ReadonlySet<string>,
): CapabilityAccess {
  if (capability === undefined || !capability.deployment_installed) return 'not-installed'
  if (!capability.tenant_entitled) return 'not-entitled'
  if (!capability.permissions.every((permission) => userPermissions.has(permission))) {
    return 'forbidden'
  }
  if (
    module === undefined
    || module.capabilityId !== capability.capability_id
    || !capability.frontend_entries.includes(module.frontendEntry)
  ) {
    return 'incompatible'
  }
  return 'allowed'
}
