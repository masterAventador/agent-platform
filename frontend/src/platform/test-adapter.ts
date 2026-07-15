import type {
  JsonValue,
  PlatformAdapter,
  SocialAccountSnapshot,
  SocialLoginSignal,
} from './types'
import { createWebPlatformAdapter } from './web'

function record(command: string, args?: unknown): void {
  const commands = Reflect.get(globalThis, '__socialCommands')
  if (Array.isArray(commands)) commands.push({ command, args })
}

export function createSocialOperationsTestAdapter(): PlatformAdapter {
  const web = createWebPlatformAdapter()
  const accounts = new Map<string, SocialAccountSnapshot>()
  const runningAccounts = new Set<string>()
  Reflect.set(globalThis, '__socialCommands', [])

  const transition = (
    accountId: string,
    signal: SocialLoginSignal,
  ): SocialAccountSnapshot => {
    const current = accounts.get(accountId)
    if (current === undefined) throw new Error('account is not prepared')
    let next: SocialAccountSnapshot
    if (['captcha_required', 'risk_control', 'login_expired'].includes(signal)) {
      next = { ...current, state: 'human_handoff', circuit_open: true }
      runningAccounts.delete(accountId)
    } else if (signal === 'begin_qr' && current.state === 'logged_out') {
      next = { ...current, state: 'awaiting_scan', circuit_open: true }
    } else if (signal === 'qr_scanned' && current.state === 'awaiting_scan') {
      next = { ...current, state: 'awaiting_confirmation', circuit_open: true }
    } else if (signal === 'authenticated' && current.state === 'awaiting_confirmation') {
      next = { ...current, state: 'healthy', circuit_open: false }
    } else if (signal === 'operator_resume' && current.state === 'human_handoff') {
      next = {
        state: 'awaiting_scan',
        circuit_open: true,
        session_revision: current.session_revision + 1,
      }
    } else if (signal === 'logout') {
      next = {
        state: 'logged_out',
        circuit_open: true,
        session_revision: current.session_revision + 1,
      }
      runningAccounts.delete(accountId)
    } else {
      throw new Error('invalid login transition')
    }
    accounts.set(accountId, next)
    return next
  }

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
        const snapshot = {
          state: 'logged_out' as const,
          circuit_open: true,
          session_revision: 0,
        }
        accounts.set(accountId, snapshot)
        return snapshot
      },
      signalLogin: async (accountId, signal: SocialLoginSignal) => {
        record('social_account_login_signal', { accountId, signal })
        return transition(accountId, signal)
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
        const snapshot = accounts.get(accountId)
        if (snapshot?.state !== 'healthy' || snapshot.circuit_open) {
          throw new Error('account is not healthy')
        }
        runningAccounts.add(accountId)
        return {
          running: true,
          protocolVersion: '1.0',
          capabilityId: 'social-operations',
        }
      },
      invokeAccount: async (accountId, request: JsonValue) => {
        record('social_account_invoke', { accountId, request })
        if (!runningAccounts.has(accountId)) throw new Error('executor is not running')
        return { status: 'accepted' }
      },
      logoutAccount: async (accountId) => {
        record('social_account_logout', { accountId })
        transition(accountId, 'logout')
      },
      emergencyStop: async (accountId) => {
        record('social_account_emergency_stop', { accountId })
        transition(accountId, 'risk_control')
      },
      takeSafeDiagnostics: async () => {
        record('social_executor_take_safe_diagnostics')
        return ['cookie=[REDACTED]']
      },
    },
  }
}
