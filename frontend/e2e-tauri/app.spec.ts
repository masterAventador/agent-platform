import { browser, expect } from '@wdio/globals'

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
    ))).rejects.toThrow()
  })
})
