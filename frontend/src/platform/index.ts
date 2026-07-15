import { isTauri } from '@tauri-apps/api/core'

import { createTauriPlatformAdapter } from './tauri'
import { createSocialOperationsTestAdapter } from './test-adapter'
import type { PlatformAdapter } from './types'
import { createWebPlatformAdapter } from './web'

let activeAdapter: PlatformAdapter | undefined

export function createPlatformAdapter(tauriRuntime: boolean): PlatformAdapter {
  return tauriRuntime ? createTauriPlatformAdapter() : createWebPlatformAdapter()
}

export function getPlatformAdapter(): PlatformAdapter {
  activeAdapter ??= createRequestedTestAdapter()
    ?? createPlatformAdapter(isTauri())
  return activeAdapter
}

function createRequestedTestAdapter(): PlatformAdapter | undefined {
  if (!import.meta.env.DEV) return undefined
  return Reflect.get(globalThis, '__AGENT_PLATFORM_TEST_ADAPTER__') === 'social-operations'
    ? createSocialOperationsTestAdapter()
    : undefined
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
