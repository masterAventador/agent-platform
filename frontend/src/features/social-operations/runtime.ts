import {
  getPlatformAdapter,
  PlatformCapabilityError,
  type JsonValue,
  type LocalExecutorStatus,
  type PlatformAdapter,
  type SocialAccountSnapshot,
  type SocialLoginSignal,
  type SocialOperationsBridge,
  type SocialPlatform,
  type SocialSidecarDownloadInput,
  type SocialSidecarInstallInput,
} from '../../platform'

export interface SocialOperationsAccountRuntime {
  prepare(): Promise<SocialAccountSnapshot>
  signalLogin(signal: SocialLoginSignal): Promise<SocialAccountSnapshot>
  storeCookies(cookies: Uint8Array): Promise<void>
  hasCookies(): Promise<boolean>
  start(): Promise<LocalExecutorStatus>
  invoke(request: JsonValue): Promise<JsonValue>
  logout(): Promise<void>
  emergencyStop(): Promise<void>
}

export interface SocialOperationsRuntime {
  installSidecar(input: SocialSidecarInstallInput): Promise<string>
  downloadSidecar(input: SocialSidecarDownloadInput): Promise<string>
  takeSafeDiagnostics(): Promise<string[]>
  account(platform: SocialPlatform, accountId: string): SocialOperationsAccountRuntime
}

function accountRuntime(
  bridge: SocialOperationsBridge,
  platform: SocialPlatform,
  accountId: string,
): SocialOperationsAccountRuntime {
  return {
    prepare: () => bridge.prepareAccount(platform, accountId),
    signalLogin: (signal) => bridge.signalLogin(accountId, signal),
    storeCookies: (cookies) => bridge.storeCookies(accountId, cookies),
    hasCookies: () => bridge.hasCookies(accountId),
    start: () => bridge.startAccount(accountId),
    invoke: (request) => bridge.invokeAccount(accountId, request),
    logout: () => bridge.logoutAccount(accountId),
    emergencyStop: () => bridge.emergencyStop(accountId),
  }
}

export function createSocialOperationsRuntime(
  platform: PlatformAdapter = getPlatformAdapter(),
): SocialOperationsRuntime {
  if (!platform.capabilities().socialOperations) {
    throw new PlatformCapabilityError('socialOperations')
  }
  const bridge = platform.socialOperations
  return {
    installSidecar: (input) => bridge.installSidecar(input),
    downloadSidecar: (input) => bridge.downloadSidecar(input),
    takeSafeDiagnostics: () => bridge.takeSafeDiagnostics(),
    account: (socialPlatform, accountId) => accountRuntime(
      bridge,
      socialPlatform,
      accountId,
    ),
  }
}
