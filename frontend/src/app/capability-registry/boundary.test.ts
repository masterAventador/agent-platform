import { describe, expect, it } from 'vitest'

const appSource = import.meta.glob('/src/app/App.tsx', {
  eager: true,
  import: 'default',
  query: '?raw',
})['/src/app/App.tsx'] as string

describe('capability registry boundary', () => {
  it('Core App 不直接导入可选 Feature 实现', () => {
    expect(appSource).not.toMatch(/features\/(?:social-operations|video-studio)/)
  })
})
