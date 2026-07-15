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
  JsonValue,
  LocalExecutorBridge,
  LocalExecutorStatus,
  PlatformAdapter,
  PlatformCapabilities,
  PlatformFile,
  PlatformNotification,
  PlatformRuntimeConfig,
  SaveFileOptions,
  SaveFileResult,
  SecureCredentialStore,
  SocialAccountSnapshot,
  SocialLoginSignal,
  SocialLoginState,
  SocialOperationsBridge,
  SocialPlatform,
  SocialSidecarDownloadInput,
  SocialSidecarInstallInput,
  SocialSidecarManifest,
} from './types'
export { PlatformCapabilityError, PlatformOperationError } from './types'
