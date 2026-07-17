import axios from 'axios'

import type { ExecutionStatus, Schedule } from '../api/scheduled-tasks'
import { formatInstantInTimezone } from './zoned-time'


export function describeSchedule(schedule: Schedule): string {
  if (schedule.kind === 'cron') {
    return `Cron ${schedule.cron_expression}（${schedule.timezone}）`
  }
  return `单次预约 ${formatInstantInTimezone(schedule.run_at, schedule.timezone)}`
}

/**
 * 尽力而为地识别「至少每 5 分钟触发一次」的 Cron 表达式。
 *
 * 这是**提示**不是门禁：C16 配额落地前平台侧对高频 + ALLOW 无任何节流，
 * 表单据此给用户可见提醒。频率硬下限归属 C16 阶段三（见路线图强制门禁②），
 * 前端不设阈值拦截，识别不出的表达式不提示即可，不影响正确性。
 */
export function isHighFrequencyCron(expression: string): boolean {
  const fields = expression.trim().split(/\s+/)
  if (fields.length < 5) return false
  const minuteField = fields[0]
  if (minuteField === '*') return true
  const stepMatch = /^\*\/(\d{1,2})$/.exec(minuteField)
  return stepMatch !== null && Number(stepMatch[1]) <= 5
}

export const executionStatusLabels: Record<ExecutionStatus, { color: string; text: string }> = {
  deferred: { color: 'default', text: '待派发' },
  dispatched: { color: 'processing', text: '已派发' },
  retry_waiting: { color: 'warning', text: '等待重试' },
  succeeded: { color: 'success', text: '成功' },
  failed: { color: 'error', text: '失败' },
  cancelled: { color: 'default', text: '已取消' },
  skipped: { color: 'default', text: '已跳过' },
}

/** 与后端 `SkipReason` 一一对应；缺项时调用方回落展示原始机器码，不是空白。 */
export const skipReasonLabels: Record<string, string> = {
  task_paused: '任务已暂停',
  misfire_skipped: '错过触发点，按策略跳过',
  misfire_window_exceeded: '超出补跑窗口',
  concurrency_skipped: '上一轮仍在执行，按并发策略跳过',
  queue_collapsed: '已有触发点在排队，合并本次',
  employee_not_runnable: '数字员工当前不可运行',
  scheduled_tasks_disabled: '发布版本未开启定时任务能力',
  creator_permission_revoked: '创建者权限已被撤销',
  input_schema_incompatible: '输入与发布版本的 Schema 不兼容',
}

/** 与后端 `PauseReason` 一一对应；缺项时调用方回落展示原始机器码。无原因表示人工暂停。 */
export const pauseReasonLabels: Record<string, string> = {
  employee_not_runnable: '数字员工当前不可运行，已自动暂停',
  scheduled_tasks_disabled: '发布版本未开启定时任务能力，已自动暂停',
  creator_permission_revoked: '创建者权限已被撤销，已自动暂停',
  input_schema_incompatible: '输入与发布版本的 Schema 不兼容，已自动暂停',
}

const errorMessages: Record<string, string> = {
  scheduled_tasks_disabled: '该数字员工的发布版本未开启定时任务能力，请先在员工编辑页开启并重新发布',
  employee_not_published: '数字员工尚未发布，请先发布后再创建定时任务',
  employee_configuration_unavailable: '数字员工配置当前不可运行，请检查其发布版本',
  invalid_cron_expression: 'Cron 表达式非法，请检查后重试',
  invalid_schedule_timezone: '时区必须是有效的 IANA 时区名',
  invalid_schedule_window: '预约时间非法，请选择一个未来的时间',
  schedule_has_no_future_occurrence: '该调度没有未来的触发时间，请调整后重试',
  run_input_too_large: '任务输入超过大小限制',
  run_input_schema_validation_failed: '任务输入不符合数字员工发布版本的输入 Schema',
  scheduled_task_conflict: '定时任务已被并发修改，请刷新后重试',
  invalid_scheduled_task_transition: '该操作与任务当前状态冲突，请刷新后重试',
  resource_not_found: '定时任务不存在或你无权访问',
}

interface ScheduledTaskErrorBody {
  detail?: { code?: string; message?: string }
}

/**
 * 把后端错误统一转成面向用户的提示。
 *
 * 有已知错误码时优先用本地文案（可操作、指明下一步）；否则回落到后端 message；
 * 都没有再用通用兜底，绝不把原始异常抛给界面。
 */
export function describeScheduledTaskError(error: unknown): string {
  if (axios.isAxiosError<ScheduledTaskErrorBody>(error)) {
    const detail = error.response?.data?.detail
    if (detail?.code && errorMessages[detail.code]) return errorMessages[detail.code]
    if (detail?.message) return detail.message
  }
  // 测试与非 axios 调用方可能传入结构相同的普通对象。
  const detail = (error as ScheduledTaskErrorBody & {
    response?: { data?: ScheduledTaskErrorBody }
  })?.response?.data?.detail
  if (detail?.code && errorMessages[detail.code]) return errorMessages[detail.code]
  if (detail?.message) return detail.message
  return '操作失败，请刷新后重试'
}
