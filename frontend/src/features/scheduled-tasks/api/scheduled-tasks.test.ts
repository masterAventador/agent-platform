import { beforeEach, describe, expect, it, vi } from 'vitest'

import { apiClient } from '../../../api/client'
import {
  createScheduledTask,
  deleteScheduledTask,
  getScheduledTask,
  listScheduledTaskExecutions,
  listScheduledTasks,
  pauseScheduledTask,
  resumeScheduledTask,
  updateScheduledTask,
} from './scheduled-tasks'


vi.mock('../../../api/client', () => ({
  apiClient: { get: vi.fn(), post: vi.fn(), patch: vi.fn(), delete: vi.fn() },
}))

const tenantId = '10000000-0000-4000-8000-000000000010'
const taskId = '20000000-0000-4000-8000-000000000020'
const employeeId = '30000000-0000-4000-8000-000000000030'
const userId = '40000000-0000-4000-8000-000000000040'

const task = {
  id: taskId,
  tenant_id: tenantId,
  employee_id: employeeId,
  created_by: userId,
  name: '每个工作日早上九点巡检',
  schedule: {
    kind: 'cron',
    timezone: 'Asia/Shanghai',
    cron_expression: '0 9 * * 1-5',
    run_at: null,
  },
  input: { task: '巡检' },
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

const execution = {
  id: '50000000-0000-4000-8000-000000000050',
  scheduled_task_id: taskId,
  scheduled_for: '2026-07-20T01:00:00Z',
  status: 'succeeded',
  attempts: 1,
  run_id: '60000000-0000-4000-8000-000000000060',
  skip_reason: null,
  error_message: null,
  next_attempt_at: null,
  created_at: '2026-07-20T01:00:00Z',
  updated_at: '2026-07-20T01:00:05Z',
}

describe('scheduled tasks API', () => {
  beforeEach(() => vi.clearAllMocks())

  it('列表返回分页任务并透传租户头与筛选参数', async () => {
    vi.mocked(apiClient.get).mockResolvedValue({
      data: { items: [task], total: 1, limit: 50, offset: 0 },
    })

    const result = await listScheduledTasks(tenantId, { employeeId })

    expect(result.total).toBe(1)
    expect(result.items[0].schedule.cron_expression).toBe('0 9 * * 1-5')
    expect(apiClient.get).toHaveBeenCalledWith('/scheduled-tasks', expect.objectContaining({
      headers: { 'X-Tenant-ID': tenantId },
      params: expect.objectContaining({ employee_id: employeeId }),
    }))
  })

  it('详情按 ID 读取单个任务', async () => {
    vi.mocked(apiClient.get).mockResolvedValue({ data: task })

    const result = await getScheduledTask(tenantId, taskId)

    expect(result.id).toBe(taskId)
    expect(apiClient.get).toHaveBeenCalledWith(
      `/scheduled-tasks/${taskId}`,
      expect.objectContaining({ headers: { 'X-Tenant-ID': tenantId } }),
    )
  })

  it('创建把 Cron 调度与输入原样提交给平台 API', async () => {
    vi.mocked(apiClient.post).mockResolvedValue({ data: task })

    const result = await createScheduledTask(tenantId, {
      employee_id: employeeId,
      name: '每个工作日早上九点巡检',
      schedule: { kind: 'cron', cron_expression: '0 9 * * 1-5', timezone: 'Asia/Shanghai' },
      input: { task: '巡检' },
      misfire_policy: 'skip',
      concurrency_policy: 'skip',
      max_retries: 3,
      retry_backoff_seconds: 60,
    })

    expect(result.id).toBe(taskId)
    expect(apiClient.post).toHaveBeenCalledWith(
      '/scheduled-tasks',
      expect.objectContaining({
        employee_id: employeeId,
        schedule: { kind: 'cron', cron_expression: '0 9 * * 1-5', timezone: 'Asia/Shanghai' },
      }),
      expect.objectContaining({ headers: { 'X-Tenant-ID': tenantId } }),
    )
  })

  it('编辑使用 PATCH 且不提交运行态字段', async () => {
    vi.mocked(apiClient.patch).mockResolvedValue({ data: task })

    await updateScheduledTask(tenantId, taskId, {
      name: '改名后的巡检',
      schedule: { kind: 'once', run_at: '2026-08-01T02:00:00Z', timezone: 'Asia/Shanghai' },
      input: {},
      misfire_policy: 'run_once',
      concurrency_policy: 'allow',
      max_retries: 0,
      retry_backoff_seconds: 30,
    })

    const [path, payload] = vi.mocked(apiClient.patch).mock.calls[0]
    expect(path).toBe(`/scheduled-tasks/${taskId}`)
    // 运行态字段归调度器所有，客户端提交它们会被后端拒绝（extra=forbid）。
    expect(payload).not.toHaveProperty('enabled')
    expect(payload).not.toHaveProperty('next_run_at')
    expect(payload).not.toHaveProperty('revision')
  })

  it('暂停与恢复调用各自的动作端点', async () => {
    vi.mocked(apiClient.post).mockResolvedValue({ data: { ...task, enabled: false } })
    await pauseScheduledTask(tenantId, taskId)
    expect(apiClient.post).toHaveBeenCalledWith(
      `/scheduled-tasks/${taskId}/pause`,
      undefined,
      expect.objectContaining({ headers: { 'X-Tenant-ID': tenantId } }),
    )

    vi.mocked(apiClient.post).mockResolvedValue({ data: task })
    await resumeScheduledTask(tenantId, taskId)
    expect(apiClient.post).toHaveBeenCalledWith(
      `/scheduled-tasks/${taskId}/resume`,
      undefined,
      expect.objectContaining({ headers: { 'X-Tenant-ID': tenantId } }),
    )
  })

  it('删除调用 DELETE 端点', async () => {
    vi.mocked(apiClient.delete).mockResolvedValue({ status: 204 })

    await deleteScheduledTask(tenantId, taskId)

    expect(apiClient.delete).toHaveBeenCalledWith(
      `/scheduled-tasks/${taskId}`,
      expect.objectContaining({ headers: { 'X-Tenant-ID': tenantId } }),
    )
  })

  it('执行记录返回分页历史', async () => {
    vi.mocked(apiClient.get).mockResolvedValue({
      data: { items: [execution], total: 1, limit: 50, offset: 0 },
    })

    const result = await listScheduledTaskExecutions(tenantId, taskId, {})

    expect(result.items[0].status).toBe('succeeded')
    expect(apiClient.get).toHaveBeenCalledWith(
      `/scheduled-tasks/${taskId}/executions`,
      expect.objectContaining({ headers: { 'X-Tenant-ID': tenantId } }),
    )
  })

  it('外部边界校验：未知执行状态一律拒绝，不进入界面', async () => {
    vi.mocked(apiClient.get).mockResolvedValue({
      data: {
        items: [{ ...execution, status: 'not-a-real-status' }],
        total: 1,
        limit: 50,
        offset: 0,
      },
    })

    await expect(listScheduledTaskExecutions(tenantId, taskId, {})).rejects.toThrow()
  })

  it('外部边界校验：缺少调度字段的任务一律拒绝', async () => {
    const { schedule: _schedule, ...withoutSchedule } = task
    vi.mocked(apiClient.get).mockResolvedValue({
      data: { items: [withoutSchedule], total: 1, limit: 50, offset: 0 },
    })

    await expect(listScheduledTasks(tenantId, {})).rejects.toThrow()
  })
})
