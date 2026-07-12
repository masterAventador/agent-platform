import { describe, expect, it } from 'vitest'

import { formatRunEvent } from './status'


describe('formatRunEvent', () => {
  it('把模型消息映射为可展示的标题和输出内容', () => {
    expect(formatRunEvent('message.output', { content: '模型生成的最终答案' })).toEqual({
      label: '模型输出',
      content: '模型生成的最终答案',
    })
  })

  it('不会把非字符串模型载荷渲染为输出内容', () => {
    expect(formatRunEvent('message.output', { content: { secret: true } })).toEqual({
      label: '模型输出',
      content: null,
    })
  })
})
