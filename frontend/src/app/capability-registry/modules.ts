import { z } from 'zod'

import {
  parseCapabilityRegistryEntries,
  resolveCapabilityAccess,
  type CapabilityAccess,
  type CapabilityRegistryEntry,
} from './registry'
import type {
  CapabilityNavigationEntry,
  FrontendCapabilityModule,
} from './types'

interface FrontendCapabilityModuleExport {
  default: FrontendCapabilityModule
}

export interface FrontendCapabilityDescriptor {
  capabilityId: string
  frontendEntries: readonly string[]
  permissions: readonly string[]
  navigation: readonly CapabilityNavigationEntry[]
  routePaths: readonly string[]
  load: () => Promise<FrontendCapabilityModule>
}

export interface ResolvedFrontendCapabilityModule {
  capability: CapabilityRegistryEntry
  descriptor: FrontendCapabilityDescriptor | undefined
  access: CapabilityAccess
  module: FrontendCapabilityModule | undefined
}

const descriptorSchema = z.object({
  schema_version: z.literal('1.0'),
  capability_id: z.string().regex(/^[a-z0-9]+(?:-[a-z0-9]+)*$/),
  frontend_entries: z.array(z.string()).min(1),
  permissions: z.array(z.string()).min(1),
  navigation: z.array(z.object({ label: z.string().min(1), path: z.string().startsWith('/') })),
  route_paths: z.array(z.string().startsWith('/')).min(1),
}).strict()

const descriptorFiles = import.meta.glob<unknown>('/src/features/*/capability.json', {
  eager: true,
  import: 'default',
})
const moduleLoaders = import.meta.glob<FrontendCapabilityModuleExport>(
  '/src/features/*/module.tsx',
)

export const frontendCapabilityDescriptors: readonly FrontendCapabilityDescriptor[] =
  Object.entries(descriptorFiles).map(([descriptorPath, input]) => {
    const manifest = descriptorSchema.parse(input)
    const modulePath = descriptorPath.replace(/\/capability\.json$/, '/module.tsx')
    const loadExport = moduleLoaders[modulePath]
    if (loadExport === undefined) throw new Error(`missing capability module: ${modulePath}`)
    return {
      capabilityId: manifest.capability_id,
      frontendEntries: manifest.frontend_entries,
      permissions: manifest.permissions,
      navigation: manifest.navigation,
      routePaths: manifest.route_paths,
      load: async () => (await loadExport()).default,
    }
  })

export async function loadAuthorizedFrontendCapabilityModules(
  input: unknown,
  userPermissions: ReadonlySet<string>,
  descriptors: readonly FrontendCapabilityDescriptor[] = frontendCapabilityDescriptors,
): Promise<ResolvedFrontendCapabilityModule[]> {
  const capabilities = parseCapabilityRegistryEntries(input)
  return Promise.all(capabilities.map(async (capability) => {
    const descriptor = descriptors.find(
      (candidate) => candidate.capabilityId === capability.capability_id,
    )
    const access = resolveCapabilityAccess(capability, descriptor, userPermissions)
    if (access !== 'allowed' || descriptor === undefined) {
      return { capability, descriptor, access, module: undefined }
    }

    const module = await descriptor.load()
    if (!moduleMatchesDescriptor(module, descriptor)) {
      return { capability, descriptor, access: 'incompatible', module: undefined }
    }
    return { capability, descriptor, access, module }
  }))
}

function moduleMatchesDescriptor(
  module: FrontendCapabilityModule,
  descriptor: FrontendCapabilityDescriptor,
): boolean {
  const moduleRoutePaths = module.routes.map((route) => route.path)
  return module.capabilityId === descriptor.capabilityId
    && descriptor.frontendEntries.includes(module.frontendEntry)
    && moduleRoutePaths.length === descriptor.routePaths.length
    && moduleRoutePaths.every((path) => descriptor.routePaths.includes(path))
}
