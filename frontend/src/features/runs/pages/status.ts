import type { RunStatus } from '../api/runs'


export const runStatusLabels: Record<RunStatus, { color: string; text: string }> = {
  queued: { color: 'default', text: '排队中' },
  running: { color: 'processing', text: '执行中' },
  waiting_for_input: { color: 'warning', text: '等待输入' },
  waiting_for_approval: { color: 'warning', text: '等待审批' },
  completed: { color: 'success', text: '已完成' },
  failed: { color: 'error', text: '失败' },
  cancelled: { color: 'default', text: '已取消' },
}

export function formatRunInput(input: Record<string, unknown>) {
  if (typeof input.message === 'string') return input.message
  return JSON.stringify(input, null, 2)
}
