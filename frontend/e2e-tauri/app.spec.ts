import { browser, expect } from '@wdio/globals'
import { randomUUID } from 'node:crypto'

const demoCredentials = {
  email: 'demo@example.com',
  password: 'agent-platform-demo',
}
const expectRememberedLogin = process.env.TAURI_EXPECT_REMEMBERED_LOGIN === '1'

async function readRememberedLogin(): Promise<string | null> {
  return browser.tauri.execute(({ core }) => (
    core.invoke<string | null>('remembered_login_get')
  ))
}

describe('Tauri 桌面客户端', () => {
  it('启动共享 React 界面并暴露真实桌面能力', async () => {
    await expect(browser).toHaveTitle('AI 数字员工平台')

    const capabilities = await browser.tauri.execute(({ core }) => (
      core.invoke<{ platform: string; secureCredentials: boolean }>('platform_capabilities')
    ))

    expect(['macos', 'windows']).toContain(capabilities.platform)
    expect(capabilities.secureCredentials).toBe(true)
  })

  it('原生凭据命令对不安全键名失败关闭', async () => {
    await expect(browser.tauri.execute(({ core }) => (
      core.invoke('credential_get', { key: '../../login.key' })
    ))).rejects.toThrow('invalid_key')
  })

  it('默认 ACL 在真实 Tauri WebView 授权社交账号命令', async () => {
    const accountId = randomUUID()
    const prepared = await browser.tauri.execute(({ core }, accountId) => (
      core.invoke<{ circuit_open: boolean; session_revision: number; state: string }>(
        'social_account_prepare',
        { platform: 'douyin', accountId },
      )
    ), accountId)

    expect(prepared).toEqual({
      circuit_open: true,
      session_revision: 0,
      state: 'logged_out',
    })

    const awaitingScan = await browser.tauri.execute(({ core }, accountId) => (
      core.invoke<{ state: string }>('social_account_login_signal', {
        accountId,
        signal: 'begin_qr',
      })
    ), accountId)
    expect(awaitingScan.state).toBe('awaiting_scan')

    await browser.tauri.execute(({ core }, accountId) => (
      core.invoke('social_account_logout', { accountId })
    ), accountId)
  })

  it('通过 App 私有数据跨启动恢复固定测试账号', async () => {
    if (!expectRememberedLogin) {
      await browser.tauri.execute(({ core }, value) => (
        core.invoke('remembered_login_set', { value })
      ), JSON.stringify(demoCredentials))
    }

    const stored = await readRememberedLogin()
    expect(stored).not.toBeNull()
    expect(JSON.parse(stored ?? '{}')).toEqual(demoCredentials)

    if (!expectRememberedLogin) return

    await browser.execute(() => {
      if (window.location.pathname !== '/login') {
        window.history.pushState({}, '', '/login')
        window.dispatchEvent(new PopStateEvent('popstate'))
      }
    })

    try {
      await browser.waitUntil(async () => browser.execute((expected) => {
        const email = document.querySelector<HTMLInputElement>('input[autocomplete="email"]')
        const password = document.querySelector<HTMLInputElement>(
          'input[autocomplete="current-password"]',
        )
        return email?.value === expected.email && password?.value === expected.password
      }, demoCredentials), {
        timeoutMsg: '登录页未从 App 私有数据恢复固定 Demo 账号',
      })
    } catch (cause) {
      const state = await browser.execute(() => ({
        url: window.location.href,
        text: document.body.innerText.slice(0, 1_000),
        email: document.querySelector<HTMLInputElement>('input[autocomplete="email"]')?.value,
        passwordLength: document.querySelector<HTMLInputElement>(
          'input[autocomplete="current-password"]',
        )?.value.length,
        diagnostic: document.querySelector<HTMLElement>('.bootstrap-error')
          ?.dataset.testDiagnostic,
      }))
      throw new Error(`登录页恢复失败；WebView 状态：${JSON.stringify(state)}`, { cause })
    }
  })

  it('通过无端口认证 Sidecar 完成本地执行器生命周期', async () => {
    const capabilityId = 'social-operations'

    try {
      const started = await browser.tauri.execute(({ core }, capabilityId) => (
        core.invoke<{ running: boolean; protocolVersion: string; capabilityId: string }>(
          'local_executor_start',
          { capabilityId },
        )
      ), capabilityId)
      expect(started).toEqual({
        running: true,
        protocolVersion: '1.0',
        capabilityId,
      })

      const response = await browser.tauri.execute(({ core }, capabilityId) => (
        core.invoke<{
          ok: boolean
          protocol_version: string
          message_type: string
          status: string
        }>('local_executor_invoke', {
          capabilityId,
          request: {
            protocol_version: '1.0',
            message_type: 'task.request',
          },
        })
      ), capabilityId)
      expect(response).toEqual({
        ok: true,
        protocol_version: '1.0',
        message_type: 'task.request',
        status: 'accepted',
      })

      const running = await browser.tauri.execute(({ core }, capabilityId) => (
        core.invoke<{ running: boolean }>('local_executor_status', { capabilityId })
      ), capabilityId)
      expect(running.running).toBe(true)
    } finally {
      const stopped = await browser.tauri.execute(({ core }, capabilityId) => (
        core.invoke<{ running: boolean }>('local_executor_stop', { capabilityId })
      ), capabilityId)
      expect(stopped.running).toBe(false)
    }
  })
})
