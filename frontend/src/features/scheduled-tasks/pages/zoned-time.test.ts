import { describe, expect, it } from 'vitest'

import {
  formatInstantInTimezone,
  supportedTimezones,
  utcToZonedWallClock,
  zonedWallClockToUtc,
} from './zoned-time'


describe('formatInstantInTimezone', () => {
  // next_run_at 是 UTC 瞬时；必须按任务自己的 IANA 时区渲染，否则用户会看到
  // 与自己填的 Cron 表达式自相矛盾的「下次执行时间」。
  it('把 UTC 瞬时渲染为任务所属时区的当地时间，而不是浏览器本地时区', () => {
    expect(formatInstantInTimezone('2026-07-20T01:00:00Z', 'Asia/Shanghai'))
      .toBe('2026-07-20 09:00 (Asia/Shanghai)')
  })

  it('同一 UTC 瞬时在不同任务时区渲染出各自的当地时间', () => {
    expect(formatInstantInTimezone('2026-07-20T01:00:00Z', 'UTC'))
      .toBe('2026-07-20 01:00 (UTC)')
    expect(formatInstantInTimezone('2026-07-20T13:00:00Z', 'America/New_York'))
      .toBe('2026-07-20 09:00 (America/New_York)')
  })

  // 跨 DST 边界：美东 3 月 8 日 02:00 本地时间进入夏令时，同一条「每天 09:00」
  // 的 Cron 在切换前后对应不同的 UTC 瞬时，但展示的当地时间必须都是 09:00。
  it('跨 DST 边界仍展示与调度语义自洽的当地时间', () => {
    expect(formatInstantInTimezone('2026-03-07T14:00:00Z', 'America/New_York'))
      .toBe('2026-03-07 09:00 (America/New_York)')
    expect(formatInstantInTimezone('2026-03-09T13:00:00Z', 'America/New_York'))
      .toBe('2026-03-09 09:00 (America/New_York)')
  })

  it('空值展示为占位符而不是 Invalid Date', () => {
    expect(formatInstantInTimezone(null, 'Asia/Shanghai')).toBe('—')
    expect(formatInstantInTimezone('not-a-date', 'Asia/Shanghai')).toBe('—')
  })
})

describe('zonedWallClockToUtc', () => {
  // 用户在单次预约里填的是「所选时区的当地时间」，提交给后端必须是等价的 UTC 瞬时。
  it('把当地时间按所选时区换算成 UTC 瞬时', () => {
    expect(zonedWallClockToUtc('2026-08-01T10:00', 'Asia/Shanghai'))
      .toBe('2026-08-01T02:00:00.000Z')
    expect(zonedWallClockToUtc('2026-08-01T10:00', 'UTC'))
      .toBe('2026-08-01T10:00:00.000Z')
  })

  it('按当地时间是否处于夏令时选择正确的偏移', () => {
    // 美东冬令时 UTC-5
    expect(zonedWallClockToUtc('2026-01-15T09:00', 'America/New_York'))
      .toBe('2026-01-15T14:00:00.000Z')
    // 美东夏令时 UTC-4
    expect(zonedWallClockToUtc('2026-07-15T09:00', 'America/New_York'))
      .toBe('2026-07-15T13:00:00.000Z')
  })

  it('往返换算稳定：UTC → 当地 → UTC 回到原点', () => {
    const instant = '2026-11-01T05:30:00.000Z'
    const wallClock = utcToZonedWallClock(instant, 'America/New_York')
    expect(zonedWallClockToUtc(wallClock, 'America/New_York')).toBe(instant)
  })

  it('非法输入返回 null，交由表单内联报错，不提交垃圾给后端', () => {
    expect(zonedWallClockToUtc('', 'Asia/Shanghai')).toBeNull()
    expect(zonedWallClockToUtc('not-a-time', 'Asia/Shanghai')).toBeNull()
  })
})

describe('utcToZonedWallClock', () => {
  it('把 UTC 瞬时还原成可直接回填表单的当地时间', () => {
    expect(utcToZonedWallClock('2026-08-01T02:00:00Z', 'Asia/Shanghai'))
      .toBe('2026-08-01T10:00')
  })
})

describe('supportedTimezones', () => {
  // 时区是不可信输入。前端只提供运行时确实支持的 IANA 名字，与后端
  // ZoneInfo 的判定保持一致（固定偏移、空值一律不在清单里）。
  it('只提供有效的 IANA 时区名', () => {
    const zones = supportedTimezones()
    expect(zones).toContain('Asia/Shanghai')
    expect(zones).toContain('UTC')
    expect(zones).not.toContain('+08:00')
    expect(zones.every((zone) => zone.length > 0)).toBe(true)
  })
})
