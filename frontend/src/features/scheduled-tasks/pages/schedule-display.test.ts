import { describe, expect, it } from 'vitest'

import type { ScheduledTask } from '../api/scheduled-tasks'
import {
  describeScheduledTaskError,
  describeSchedule,
  executionStatusLabels,
  isHighFrequencyCron,
  pauseReasonLabels,
  skipReasonLabels,
} from './schedule-display'


const cronTask: ScheduledTask = {
  id: '20000000-0000-4000-8000-000000000020',
  tenant_id: '10000000-0000-4000-8000-000000000010',
  employee_id: '30000000-0000-4000-8000-000000000030',
  created_by: '40000000-0000-4000-8000-000000000040',
  name: '每个工作日早上九点巡检',
  schedule: {
    kind: 'cron',
    timezone: 'Asia/Shanghai',
    cron_expression: '0 9 * * 1-5',
    run_at: null,
  },
  input: {},
  enabled: true,
  pause_reason: null,
  next_run_at: '2026-07-20T01:00:00Z',
  last_run_at: null,
  misfire_policy: 'skip',
  concurrency_policy: 'skip',
  max_retries: 3,
  retry_backoff_seconds: 60,
  revision: 1,
  created_at: '2026-07-17T08:00:00Z',
}

describe('describeSchedule', () => {
  it('Cron 调度展示表达式与时区', () => {
    expect(describeSchedule(cronTask.schedule)).toBe('Cron 0 9 * * 1-5（Asia/Shanghai）')
  })

  it('单次预约展示所属时区的当地时间', () => {
    expect(describeSchedule({
      kind: 'once',
      timezone: 'Asia/Shanghai',
      cron_expression: null,
      run_at: '2026-08-01T02:00:00Z',
    })).toBe('单次预约 2026-08-01 10:00 (Asia/Shanghai)')
  })
})

describe('isHighFrequencyCron', () => {
  // C16 配额落地前平台侧无节流，高频 + ALLOW 会持续烧模型额度。
  // 这是尽力而为的提示（不是门禁），只识别分钟字段明确高频的表达式。
  it.each(['* * * * *', '*/1 * * * *', '*/5 * * * *'])('识别高频表达式 %s', (expression) => {
    expect(isHighFrequencyCron(expression)).toBe(true)
  })

  it.each(['0 9 * * 1-5', '*/30 * * * *', '30 2 * * *', '不是 cron'])(
    '不把 %s 误判为高频',
    (expression) => {
      expect(isHighFrequencyCron(expression)).toBe(false)
    },
  )
})

describe('describeScheduledTaskError', () => {
  it('把后端错误码转成可操作的中文提示', () => {
    expect(describeScheduledTaskError({
      isAxiosError: true,
      response: { status: 409, data: { detail: { code: 'scheduled_tasks_disabled' } } },
    })).toContain('未开启定时任务')
    expect(describeScheduledTaskError({
      isAxiosError: true,
      response: { status: 422, data: { detail: { code: 'invalid_cron_expression' } } },
    })).toContain('Cron 表达式')
    expect(describeScheduledTaskError({
      isAxiosError: true,
      response: { status: 409, data: { detail: { code: 'scheduled_task_conflict' } } },
    })).toContain('并发修改')
  })

  it('未知错误回落到通用提示而不是暴露原始异常', () => {
    expect(describeScheduledTaskError(new Error('boom'))).toBe('操作失败，请刷新后重试')
  })
})

describe('状态与原因文案', () => {
  it('覆盖后端全部执行状态，避免界面出现空白标签', () => {
    for (const status of [
      'deferred', 'dispatched', 'retry_waiting', 'succeeded', 'failed', 'cancelled', 'skipped',
    ] as const) {
      expect(executionStatusLabels[status].text).toBeTruthy()
    }
  })

  it('覆盖后端全部跳过原因与自动暂停原因', () => {
    for (const reason of [
      'task_paused', 'misfire_skipped', 'misfire_window_exceeded', 'concurrency_skipped',
      'queue_collapsed', 'employee_not_runnable', 'scheduled_tasks_disabled',
      'creator_permission_revoked', 'input_schema_incompatible',
    ]) {
      expect(skipReasonLabels[reason]).toBeTruthy()
    }
    for (const reason of [
      'employee_not_runnable', 'scheduled_tasks_disabled',
      'creator_permission_revoked', 'input_schema_incompatible',
    ]) {
      expect(pauseReasonLabels[reason]).toBeTruthy()
    }
  })
})
