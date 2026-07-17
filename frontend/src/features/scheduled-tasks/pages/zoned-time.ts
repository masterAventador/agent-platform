/**
 * IANA 时区换算（纯函数，只依赖运行时的 Intl 数据）。
 *
 * 调度按任务自己的时区解释民用时间，而 API 只交换 UTC 瞬时，因此界面必须
 * 在这两者之间做换算——既不能拿浏览器本地时区渲染，也不能把用户填的当地
 * 时间当 UTC 直接提交。
 */

export const EMPTY_PLACEHOLDER = '—'

const WALL_CLOCK_PATTERN = /^(\d{4})-(\d{2})-(\d{2})[T ](\d{2}):(\d{2})$/

/** `sv-SE` 的日期时间格式本身就是 `YYYY-MM-DD HH:mm`，无需再拼装。 */
function zonedParts(moment: Date, timeZone: string): string {
  return new Intl.DateTimeFormat('sv-SE', {
    timeZone,
    year: 'numeric',
    month: '2-digit',
    day: '2-digit',
    hour: '2-digit',
    minute: '2-digit',
    second: '2-digit',
    hour12: false,
  }).format(moment)
}

/** 某个 UTC 瞬时在指定时区的偏移（毫秒，东为正）。 */
function timezoneOffsetMs(moment: Date, timeZone: string): number {
  const local = zonedParts(moment, timeZone)
  const asIfUtc = Date.parse(`${local.replace(' ', 'T')}Z`)
  return asIfUtc - moment.getTime()
}

/**
 * 把 UTC 瞬时渲染为**任务自己的 IANA 时区**的当地时间。
 *
 * 不用浏览器本地时区：调度按任务时区的民用时间解释，若按浏览器时区渲染，
 * 一条 `0 9 * * 1-5 / Asia/Shanghai` 的任务在其他时区的用户看来会显示成
 * 「下次执行 03:00」，与他自己填的表达式自相矛盾。
 */
export function formatInstantInTimezone(
  instant: string | null | undefined,
  timezone: string,
): string {
  if (!instant) return EMPTY_PLACEHOLDER
  const moment = new Date(instant)
  if (Number.isNaN(moment.getTime())) return EMPTY_PLACEHOLDER
  const local = zonedParts(moment, timezone)
  return `${local.slice(0, 16)} (${timezone})`
}

/** UTC 瞬时 → 可直接回填 `datetime-local` 表单的当地时间。 */
export function utcToZonedWallClock(instant: string, timezone: string): string {
  const moment = new Date(instant)
  if (Number.isNaN(moment.getTime())) return ''
  return zonedParts(moment, timezone).slice(0, 16).replace(' ', 'T')
}

/**
 * 当地时间（用户在所选时区里填的）→ UTC 瞬时。
 *
 * 先把当地时间当作 UTC 得到一个近似点，用该点的偏移回推；由于偏移本身随
 * DST 变化，再用回推结果处的偏移校正一次，覆盖跨 DST 边界的输入。
 * 春季不存在的当地时间会落到切换后的等价瞬时——与后端 `Schedule.once`
 * 接受 aware datetime 后直接转 UTC 的语义一致，不额外收紧。
 */
export function zonedWallClockToUtc(wallClock: string, timezone: string): string | null {
  const match = WALL_CLOCK_PATTERN.exec(wallClock.trim())
  if (!match) return null
  const asIfUtc = Date.parse(`${wallClock.trim().replace(' ', 'T')}:00Z`)
  if (Number.isNaN(asIfUtc)) return null
  const firstGuess = asIfUtc - timezoneOffsetMs(new Date(asIfUtc), timezone)
  const corrected = asIfUtc - timezoneOffsetMs(new Date(firstGuess), timezone)
  return new Date(corrected).toISOString()
}

/**
 * 运行时确实支持的 IANA 时区清单。
 *
 * 时区是不可信输入：只让用户从运行时支持的 IANA 名字里选，与后端 `ZoneInfo`
 * 的判定保持一致——固定偏移（`+08:00`）无法表达 DST，两侧都不接受。
 */
export function supportedTimezones(): string[] {
  const supported = Intl.supportedValuesOf('timeZone')
  return supported.includes('UTC') ? supported : ['UTC', ...supported]
}
