import { z } from 'zod'

import { apiClient } from '../../../api/client'
import { tenantRequestConfig } from '../../../api/tenant'


/** 与后端 `platform/scheduling/entities.py` 的枚举保持一致；放宽或收紧都会与调度器语义脱节。 */
export const misfirePolicies = ['skip', 'run_once', 'run_all'] as const
export const concurrencyPolicies = ['allow', 'skip', 'queue'] as const
export const executionStatuses = [
  'deferred',
  'dispatched',
  'retry_waiting',
  'succeeded',
  'failed',
  'cancelled',
  'skipped',
] as const

const cronScheduleSchema = z.object({
  kind: z.literal('cron'),
  timezone: z.string(),
  cron_expression: z.string(),
  run_at: z.null(),
})

const onceScheduleSchema = z.object({
  kind: z.literal('once'),
  timezone: z.string(),
  cron_expression: z.null(),
  run_at: z.string(),
})

const scheduleSchema = z.discriminatedUnion('kind', [cronScheduleSchema, onceScheduleSchema])

const scheduledTaskSchema = z.object({
  id: z.uuid(),
  tenant_id: z.uuid(),
  employee_id: z.uuid(),
  created_by: z.uuid(),
  name: z.string(),
  schedule: scheduleSchema,
  input: z.record(z.string(), z.unknown()),
  enabled: z.boolean(),
  pause_reason: z.string().nullable(),
  next_run_at: z.string().nullable(),
  last_run_at: z.string().nullable(),
  misfire_policy: z.enum(misfirePolicies),
  concurrency_policy: z.enum(concurrencyPolicies),
  max_retries: z.number().int().nonnegative(),
  retry_backoff_seconds: z.number().int().positive(),
  revision: z.number().int().positive(),
  created_at: z.string(),
})

const scheduledTaskListSchema = z.object({
  items: z.array(scheduledTaskSchema),
  total: z.number().int().nonnegative(),
  limit: z.number().int().positive(),
  offset: z.number().int().nonnegative(),
})

const scheduledTaskExecutionSchema = z.object({
  id: z.uuid(),
  scheduled_task_id: z.uuid(),
  scheduled_for: z.string(),
  status: z.enum(executionStatuses),
  attempts: z.number().int().nonnegative(),
  run_id: z.uuid().nullable(),
  skip_reason: z.string().nullable(),
  error_message: z.string().nullable(),
  next_attempt_at: z.string().nullable(),
  created_at: z.string(),
  updated_at: z.string(),
})

const scheduledTaskExecutionListSchema = z.object({
  items: z.array(scheduledTaskExecutionSchema),
  total: z.number().int().nonnegative(),
  limit: z.number().int().positive(),
  offset: z.number().int().nonnegative(),
})

export type ScheduledTask = z.infer<typeof scheduledTaskSchema>
export type ScheduledTaskList = z.infer<typeof scheduledTaskListSchema>
export type ScheduledTaskExecution = z.infer<typeof scheduledTaskExecutionSchema>
export type ScheduledTaskExecutionList = z.infer<typeof scheduledTaskExecutionListSchema>
export type Schedule = z.infer<typeof scheduleSchema>
export type MisfirePolicy = ScheduledTask['misfire_policy']
export type ConcurrencyPolicy = ScheduledTask['concurrency_policy']
export type ExecutionStatus = ScheduledTaskExecution['status']

/** 写入契约：请求体不携带运行态字段（enabled/next_run_at/revision 归调度器所有）。 */
export interface CronScheduleRequest {
  kind: 'cron'
  cron_expression: string
  timezone: string
}

export interface OnceScheduleRequest {
  kind: 'once'
  run_at: string
  timezone: string
}

export type ScheduleRequest = CronScheduleRequest | OnceScheduleRequest

export interface ScheduledTaskWriteRequest {
  name: string
  schedule: ScheduleRequest
  input: Record<string, unknown>
  misfire_policy: MisfirePolicy
  concurrency_policy: ConcurrencyPolicy
  max_retries: number
  retry_backoff_seconds: number
}

export type CreateScheduledTaskRequest = ScheduledTaskWriteRequest & { employee_id: string }

export interface ListScheduledTasksParams {
  employeeId?: string
  limit?: number
  offset?: number
}

export interface ListExecutionsParams {
  limit?: number
  offset?: number
}

export async function listScheduledTasks(
  tenantId: string,
  params: ListScheduledTasksParams,
): Promise<ScheduledTaskList> {
  const response = await apiClient.get<unknown>('/scheduled-tasks', {
    ...tenantRequestConfig(tenantId),
    params: {
      ...(params.employeeId ? { employee_id: params.employeeId } : {}),
      limit: params.limit ?? 50,
      offset: params.offset ?? 0,
    },
  })
  return scheduledTaskListSchema.parse(response.data)
}

export async function getScheduledTask(
  tenantId: string,
  taskId: string,
): Promise<ScheduledTask> {
  const response = await apiClient.get<unknown>(
    `/scheduled-tasks/${taskId}`,
    tenantRequestConfig(tenantId),
  )
  return scheduledTaskSchema.parse(response.data)
}

export async function createScheduledTask(
  tenantId: string,
  request: CreateScheduledTaskRequest,
): Promise<ScheduledTask> {
  const response = await apiClient.post<unknown>(
    '/scheduled-tasks',
    request,
    tenantRequestConfig(tenantId),
  )
  return scheduledTaskSchema.parse(response.data)
}

export async function updateScheduledTask(
  tenantId: string,
  taskId: string,
  request: ScheduledTaskWriteRequest,
): Promise<ScheduledTask> {
  const response = await apiClient.patch<unknown>(
    `/scheduled-tasks/${taskId}`,
    request,
    tenantRequestConfig(tenantId),
  )
  return scheduledTaskSchema.parse(response.data)
}

export async function pauseScheduledTask(
  tenantId: string,
  taskId: string,
): Promise<ScheduledTask> {
  const response = await apiClient.post<unknown>(
    `/scheduled-tasks/${taskId}/pause`,
    undefined,
    tenantRequestConfig(tenantId),
  )
  return scheduledTaskSchema.parse(response.data)
}

export async function resumeScheduledTask(
  tenantId: string,
  taskId: string,
): Promise<ScheduledTask> {
  const response = await apiClient.post<unknown>(
    `/scheduled-tasks/${taskId}/resume`,
    undefined,
    tenantRequestConfig(tenantId),
  )
  return scheduledTaskSchema.parse(response.data)
}

export async function deleteScheduledTask(tenantId: string, taskId: string): Promise<void> {
  await apiClient.delete(`/scheduled-tasks/${taskId}`, tenantRequestConfig(tenantId))
}

export async function listScheduledTaskExecutions(
  tenantId: string,
  taskId: string,
  params: ListExecutionsParams,
): Promise<ScheduledTaskExecutionList> {
  const response = await apiClient.get<unknown>(`/scheduled-tasks/${taskId}/executions`, {
    ...tenantRequestConfig(tenantId),
    params: { limit: params.limit ?? 50, offset: params.offset ?? 0 },
  })
  return scheduledTaskExecutionListSchema.parse(response.data)
}
