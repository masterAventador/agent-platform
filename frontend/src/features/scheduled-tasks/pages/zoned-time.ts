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
 * 当地时间在 DST 边界上的消歧策略（语义与 `Temporal.ZonedDateTime` 一致）。
 *
 * - `compatible`（默认）：跳变缺口内的时间**后移**到切换后的等价瞬时；重复的
 *   时间取**第一次**出现。
 * - `earlier`：缺口取切换前一侧；重复取第一次出现。
 * - `later`：缺口取切换后一侧；重复取第二次出现。
 */
export type Disambiguation = 'compatible' | 'earlier' | 'later'

/** 某个 UTC 瞬时在指定时区渲染出的当地时间（`YYYY-MM-DDTHH:mm`）。 */
function wallClockAt(instant: number, timeZone: string): string {
  return zonedParts(new Date(instant), timeZone).slice(0, 16).replace(' ', 'T')
}

/**
 * 当地时间（用户在所选时区里填的）→ UTC 瞬时。
 *
 * **不能用「把当地时间当 UTC 再按偏移回推、迭代校正」的做法**：那等价于解不动点
 * 方程 `offset(t) = asIfUtc - t`，而该方程在春季跳变缺口内**无解**、在秋季重复
 * 小时内**有两解**，迭代既不会收敛也不会报错，只会静默返回一个不自洽的瞬时。
 *
 * 这里改为枚举 + 校验：用边界两侧的偏移各算一个候选，再用「把候选渲染回当地时间
 * 是否等于用户所填」来判定候选是否真实存在——
 * - 恰好 1 个候选成立 → 普通情况，直接返回；
 * - 2 个候选都成立 → 重复小时（fall-back），按 `disambiguation` 取前/后；
 * - 0 个候选成立 → 跳变缺口（spring-forward），该当地时间不存在，按
 *   `disambiguation` 显式选择切换前/后一侧，而不是撞出一个错误答案。
 *
 * 偏移取当地时间前后各一天处的值：DST 切换间隔远大于一天，足以覆盖两侧；
 * 也因此天然支持非整小时的 DST（如 `Australia/Lord_Howe` 的 30 分钟）。
 */
export function zonedWallClockToUtc(
  wallClock: string,
  timezone: string,
  disambiguation: Disambiguation = 'compatible',
): string | null {
  const normalized = wallClock.trim().replace(' ', 'T')
  if (!WALL_CLOCK_PATTERN.exec(normalized)) return null
  const asIfUtc = Date.parse(`${normalized}:00Z`)
  if (Number.isNaN(asIfUtc)) return null

  const day = 86_400_000
  const candidates = [
    asIfUtc - timezoneOffsetMs(new Date(asIfUtc - day), timezone),
    asIfUtc - timezoneOffsetMs(new Date(asIfUtc + day), timezone),
  ]
  const ordered = [...new Set(candidates)].sort((left, right) => left - right)
  const existing = ordered.filter((instant) => wallClockAt(instant, timezone) === normalized)

  if (existing.length === 1) return new Date(existing[0]).toISOString()
  if (existing.length > 1) {
    // 重复小时：两个瞬时都真实存在，必须由调用方显式选择，不能靠巧合。
    const picked = disambiguation === 'later' ? existing[existing.length - 1] : existing[0]
    return new Date(picked).toISOString()
  }
  // 跳变缺口：该当地时间不存在。compatible/later 后移到切换后一侧，earlier 取切换前一侧。
  const picked = disambiguation === 'earlier' ? ordered[0] : ordered[ordered.length - 1]
  return new Date(picked).toISOString()
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
