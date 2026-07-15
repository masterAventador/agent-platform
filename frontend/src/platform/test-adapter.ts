import type { JsonValue, PlatformAdapter, SocialLoginSignal } from './types'
import { createWebPlatformAdapter } from './web'

function record(command: string, args?: unknown): void {
  const commands = Reflect.get(globalThis, '__socialCommands')
  if (Array.isArray(commands)) commands.push({ command, args })
}

export function createSocialOperationsTestAdapter(): PlatformAdapter {
  const web = createWebPlatformAdapter()
  Reflect.set(globalThis, '__socialCommands', [])

  return {
    ...web,
    capabilities: () => ({
      ...web.capabilities(),
      socialOperations: true,
    }),
    socialOperations: {
      installSidecar: async (input) => {
        record('social_sidecar_install', input)
        return input.manifest.version
      },
      downloadSidecar: async (input) => {
        record('social_sidecar_download', input)
        return input.manifest.version
      },
      prepareAccount: async (platform, accountId) => {
        record('social_account_prepare', { platform, accountId })
        return { state: 'logged_out', circuit_open: true, session_revision: 0 }
      },
      signalLogin: async (accountId, signal: SocialLoginSignal) => {
        record('social_account_login_signal', { accountId, signal })
        return { state: 'healthy', circuit_open: false, session_revision: 1 }
      },
      storeCookies: async (accountId, cookies) => {
        record('social_account_store_cookies', { accountId, cookies })
      },
      hasCookies: async (accountId) => {
        record('social_account_has_cookies', { accountId })
        return true
      },
      startAccount: async (accountId) => {
        record('social_account_start', { accountId })
        return {
          running: true,
          protocolVersion: '1.0',
          capabilityId: 'social-operations',
        }
      },
      invokeAccount: async (accountId, request: JsonValue) => {
        record('social_account_invoke', { accountId, request })
        return { status: 'accepted' }
      },
      logoutAccount: async (accountId) => {
        record('social_account_logout', { accountId })
      },
      emergencyStop: async (accountId) => {
        record('social_account_emergency_stop', { accountId })
      },
      takeSafeDiagnostics: async () => {
        record('social_executor_take_safe_diagnostics')
        return ['cookie=[REDACTED]']
      },
    },
  }
}
