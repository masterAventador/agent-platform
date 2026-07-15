import { browser, expect } from '@wdio/globals'

const mvpWebUrl = process.env.TAURI_MVP_WEB_URL
const describeMvp = mvpWebUrl ? describe : describe.skip
const demoEmail = 'demo@example.com'
const demoPassword = 'agent-platform-demo'

async function clickText(tag: string, text: string) {
  // The Tauri window is deliberately hidden in CI and local automation, so
  // WebDriver's viewport/focus-based visibility heuristics are not reliable.
  // Resolve and activate the control in the WebView DOM instead.
  try {
    await browser.waitUntil(async () => browser.execute((tagName, label) => {
      const element = Array.from(document.querySelectorAll<HTMLElement>(tagName))
        .find((candidate) => candidate.textContent?.trim() === label)
      return Boolean(element && !element.hasAttribute('disabled'))
    }, tag, text), { timeoutMsg: `${text} control was not ready in the WebView DOM` })
  } catch (cause) {
    const pageState = await browser.execute(() => ({
      url: window.location.href,
      text: document.body.innerText.slice(0, 1_500),
    }))
    throw new Error(
      `${text} control was not ready; WebView state: ${JSON.stringify(pageState)}`,
      { cause },
    )
  }
  await browser.execute((tagName, label) => {
    const element = Array.from(document.querySelectorAll<HTMLElement>(tagName))
      .find((candidate) => candidate.textContent?.trim() === label)
    if (!element) throw new Error(`${label} control disappeared from the WebView DOM`)
    element.click()
  }, tag, text)
}

async function submitForm(selector: string) {
  await browser.waitUntil(async () => browser.execute((formSelector) => {
    const form = document.querySelector(formSelector)
    const submit = form?.querySelector<HTMLButtonElement>('button[type="submit"]')
    return form instanceof HTMLFormElement && Boolean(submit && !submit.disabled)
  }, selector), { timeoutMsg: `${selector} was not ready to submit in the WebView DOM` })
  await browser.execute((formSelector) => {
    const form = document.querySelector(formSelector)
    if (!(form instanceof HTMLFormElement)) {
      throw new Error(`${formSelector} disappeared from the WebView DOM`)
    }
    form.requestSubmit()
  }, selector)
}

describeMvp('Tauri MVP 核心业务链路', () => {
  it('在真实桌面宿主中登录固定测试账号、发布员工、发起任务并查看工作台', async () => {
    if (!mvpWebUrl) throw new Error('TAURI_MVP_WEB_URL is required')
    await browser.waitUntil(
      async () => (await browser.getUrl()).startsWith(mvpWebUrl),
      { timeout: 30_000, timeoutMsg: '桌面客户端未跳转到受配置的 MVP Web 地址' },
    )

    // Explicitly pin the embedded session to the hidden main window. The Tauri
    // service then skips its focus-based window discovery, which cannot work
    // for an intentionally invisible AppKit window.
    await browser.tauri.switchWindow('main')
    await browser.url(`${mvpWebUrl}/login`)
    const emailInput = await $('input[autocomplete="email"]')
    await emailInput.waitForDisplayed()
    const currentPasswordInput = await $('input[autocomplete="current-password"]')
    await emailInput.setValue(demoEmail)
    await currentPasswordInput.setValue(demoPassword)
    await submitForm('form')
    await clickText('a', '数字员工')

    await clickText('button', '创建数字员工')

    const employeeNameInput = await $('input[placeholder="例如：市场研究专员"]')
    await employeeNameInput.waitForDisplayed()
    await employeeNameInput.setValue(`Tauri MVP 验收专员 ${Date.now()}`)
    await $('#roleDescription').setValue('验证桌面宿主中的完整数字员工核心流程')
    await $('#systemPrompt').setValue('完成任务后直接返回简短结果。')
    await clickText('button', '保存草稿')
    await clickText('button', '发布员工')
    await expect($('span=已发布')).toBeDisplayed()

    await clickText('button', '发起任务')
    await $('textarea[aria-label="任务内容"]').setValue('mvp-web-flow')
    await clickText('button', '确认发起')
    await expect($('span=已完成')).toBeDisplayed({ wait: 120_000 })
    await expect($('span=local stub completion')).toBeDisplayed()

    await clickText('a', '工作台')
    await expect($('h2=工作台')).toBeDisplayed()
    const completedRuns = await $('[aria-label="已完成任务"]')
    await browser.waitUntil(
      async () => Number.parseInt((await completedRuns.getText()).replace(/\D/g, ''), 10) >= 1,
      { timeoutMsg: '工作台未计入刚完成的桌面验收任务' },
    )
  })
})
