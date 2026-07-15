import { z } from 'zod'

const resourceDeclaration = z.string().regex(/^[a-z][a-z0-9_]*(?:\.[a-z][a-z0-9_]*)+$/)

export const resourceDeclarationsSchema = z.array(resourceDeclaration).min(1).superRefine(
  (declarations, context) => {
    if (new Set(declarations).size !== declarations.length) {
      context.addIssue({ code: 'custom', message: 'duplicate resource declaration' })
    }
  },
)

const capabilityRegistryEntrySchema = z.object({
  capability_id: z.string().regex(/^[a-z0-9]+(?:-[a-z0-9]+)*$/),
  deployment_installed: z.boolean(),
  tenant_entitled: z.boolean(),
  frontend_entries: resourceDeclarationsSchema,
  permissions: resourceDeclarationsSchema,
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

export function parseCapabilityRegistryEntries(input: unknown): CapabilityRegistryEntry[] {
  return z.array(capabilityRegistryEntrySchema).parse(input)
}

export interface CapabilityAccessDescriptor {
  capabilityId: string
  frontendEntries: readonly string[]
  permissions: readonly string[]
}

export function resolveCapabilityAccess(
  capability: CapabilityRegistryEntry | undefined,
  descriptor: CapabilityAccessDescriptor | undefined,
  userPermissions: ReadonlySet<string>,
): CapabilityAccess {
  if (capability === undefined || !capability.deployment_installed) return 'not-installed'
  if (!capability.tenant_entitled) return 'not-entitled'
  if (
    descriptor === undefined
    || descriptor.capabilityId !== capability.capability_id
    || !sameDeclarations(capability.frontend_entries, descriptor.frontendEntries)
    || !sameDeclarations(capability.permissions, descriptor.permissions)
  ) {
    return 'incompatible'
  }
  if (!descriptor.permissions.every((permission) => userPermissions.has(permission))) {
    return 'forbidden'
  }
  return 'allowed'
}

function sameDeclarations(actual: readonly string[], expected: readonly string[]): boolean {
  const actualDeclarations = new Set(actual)
  const expectedDeclarations = new Set(expected)
  return actualDeclarations.size === actual.length
    && expectedDeclarations.size === expected.length
    && actualDeclarations.size === expectedDeclarations.size
    && [...actualDeclarations].every((declaration) => expectedDeclarations.has(declaration))
}
