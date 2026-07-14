import { isTauri } from '@tauri-apps/api/core'

import { createTauriPlatformAdapter } from './tauri'
import type { PlatformAdapter } from './types'
import { createWebPlatformAdapter } from './web'

let activeAdapter: PlatformAdapter | undefined

export function createPlatformAdapter(tauriRuntime: boolean): PlatformAdapter {
  return tauriRuntime ? createTauriPlatformAdapter() : createWebPlatformAdapter()
}

export function getPlatformAdapter(): PlatformAdapter {
  activeAdapter ??= createPlatformAdapter(isTauri())
  return activeAdapter
}

export type {
  FileSelectionOptions,
  PlatformAdapter,
  PlatformCapabilities,
  PlatformFile,
  PlatformNotification,
  SaveFileOptions,
  SaveFileResult,
  SecureCredentialStore,
} from './types'
export { PlatformCapabilityError, PlatformOperationError } from './types'
