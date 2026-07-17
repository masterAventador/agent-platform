import { expect, test } from '@playwright/test'

import { registerAndLogin } from './helpers/auth'

test('打开平台并显示后端服务正常', async ({ page }) => {
  await registerAndLogin(page)

  await expect(page.getByRole('heading', { name: 'AI 数字员工平台' })).toBeVisible()
  await expect(page.getByRole('link', { name: '数字员工' })).toBeVisible()
  await expect(page.getByText('后端服务正常')).toBeVisible()
})

test('左侧导航与右侧内容独立滚动', async ({ page }) => {
  await page.setViewportSize({ width: 1280, height: 360 })
  await registerAndLogin(page)

  await expect(page.locator('.app-sidebar')).toBeVisible()
  await expect(page.locator('.app-content')).toBeVisible()

  const scrollState = await page.evaluate(() => {
    const shell = document.querySelector<HTMLElement>('.app-shell')
    const sidebar = document.querySelector<HTMLElement>('.app-sidebar')
    const content = document.querySelector<HTMLElement>('.app-content')

    if (shell === null || sidebar === null || content === null) {
      throw new Error('应用布局滚动区域不存在')
    }

    sidebar.scrollTop = 80
    const sidebarAfterOwnScroll = sidebar.scrollTop
    const contentAfterSidebarScroll = content.scrollTop

    content.scrollTop = 80

    return {
      windowScrollY: window.scrollY,
      sidebarAfterOwnScroll,
      sidebarAfterContentScroll: sidebar.scrollTop,
      contentAfterSidebarScroll,
      contentAfterOwnScroll: content.scrollTop,
      shellOverscroll: getComputedStyle(shell).overscrollBehavior,
      sidebarOverscroll: getComputedStyle(sidebar).overscrollBehaviorY,
      contentOverscroll: getComputedStyle(content).overscrollBehaviorY,
    }
  })

  expect(scrollState.windowScrollY).toBe(0)
  expect(scrollState.sidebarAfterOwnScroll).toBeGreaterThan(0)
  expect(scrollState.contentAfterSidebarScroll).toBe(0)
  expect(scrollState.contentAfterOwnScroll).toBeGreaterThan(0)
  expect(scrollState.sidebarAfterContentScroll).toBe(scrollState.sidebarAfterOwnScroll)
  expect(scrollState.shellOverscroll).toBe('none')
  expect(scrollState.sidebarOverscroll).toBe('contain')
  expect(scrollState.contentOverscroll).toBe('contain')
})
