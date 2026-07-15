import type { FrontendCapabilityModule } from './types'

interface FrontendCapabilityModuleExport {
  default: FrontendCapabilityModule
}

const moduleLoaders = import.meta.glob<FrontendCapabilityModuleExport>(
  '/src/features/*/module.tsx',
)

export async function loadFrontendCapabilityModule(
  capabilityId: string,
): Promise<FrontendCapabilityModule | undefined> {
  const loader = moduleLoaders[`/src/features/${capabilityId}/module.tsx`]
  if (loader === undefined) return undefined
  const module = (await loader()).default
  return module.capabilityId === capabilityId ? module : undefined
}
