import { describe, expect, it } from 'vitest'

const sourceModules = import.meta.glob('/src/**/*.{ts,tsx}', {
  eager: true,
  import: 'default',
  query: '?raw',
}) as Record<string, string>

describe('PlatformAdapter 架构边界', () => {
  it('业务源码不直接访问 Tauri API 或运行环境标记', async () => {
    const violations: string[] = []

    for (const [projectPath, source] of Object.entries(sourceModules)) {
      if (projectPath.startsWith('/src/platform/')) continue
      if (/@tauri-apps\//.test(source) || /window\.__TAURI__/.test(source) || /\bisTauri\b/.test(source)) {
        violations.push(projectPath)
      }
    }

    expect(violations).toEqual([])
  })
})
