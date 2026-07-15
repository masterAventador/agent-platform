import { beforeEach, describe, expect, it, vi } from 'vitest'

import { showBootstrapError } from './bootstrap-error'

describe('bootstrap error fallback', () => {
  beforeEach(() => {
    document.body.innerHTML = '<div id="root"><span>旧页面</span></div>'
  })

  it('replaces a failed root with a safe actionable message', () => {
    const consoleError = vi.spyOn(console, 'error').mockImplementation(() => undefined)

    showBootstrapError(new Error('secret diagnostic detail'))

    expect(document.querySelector('[role="alert"]')).toHaveTextContent('客户端启动失败')
    expect(document.getElementById('root')).toHaveTextContent('请重新启动客户端')
    expect(document.body).not.toHaveTextContent('secret diagnostic detail')
    expect(consoleError).toHaveBeenCalledWith(
      'frontend_bootstrap_failed',
      expect.any(Error),
    )
  })
})
