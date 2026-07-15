import { describe, expect, it } from 'vitest'

import { createSocialOperationsTestAdapter } from './test-adapter'

describe('Social Operations 无头 E2E 适配器', () => {
  it('执行真实扫码状态转换并拒绝跳过扫码确认', async () => {
    const account = createSocialOperationsTestAdapter().socialOperations

    await expect(account.prepareAccount('douyin', 'account-1')).resolves.toEqual(
      expect.objectContaining({ state: 'logged_out' }),
    )
    await expect(account.signalLogin('account-1', 'authenticated')).rejects.toThrow()
    await expect(account.signalLogin('account-1', 'begin_qr')).resolves.toEqual(
      expect.objectContaining({ state: 'awaiting_scan' }),
    )
    await expect(account.signalLogin('account-1', 'qr_scanned')).resolves.toEqual(
      expect.objectContaining({ state: 'awaiting_confirmation' }),
    )
    await expect(account.signalLogin('account-1', 'authenticated')).resolves.toEqual(
      expect.objectContaining({ state: 'healthy', circuit_open: false }),
    )
  })
})
