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

export interface RunEventPresentation {
  label: string
  content: string | null
}

export function formatRunEvent(
  type: string,
  payload: Record<string, unknown>,
): RunEventPresentation {
  if (payload.action === 'cancel') {
    return { label: '请求取消任务', content: null }
  }
  const labels: Record<string, string> = {
    'run.started': '任务开始执行',
    'run.progress': '任务取得新进展',
    'message.output': '模型输出',
    'run.completed': '任务执行完成',
    'run.failed': '任务执行失败',
    'run.cancelled': '任务已取消',
    'approval.required': '任务等待审批',
  }
  return {
    label: labels[type] ?? type,
    content: type === 'message.output' && typeof payload.content === 'string'
      ? payload.content
      : null,
  }
}
