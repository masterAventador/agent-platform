import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'

import { tenantMutationKey } from '../../../api/tenant'
import { useActiveWorkspaceId } from '../../workspaces/store'
import {
  createScheduledTask,
  deleteScheduledTask,
  getScheduledTask,
  listScheduledTaskExecutions,
  listScheduledTasks,
  pauseScheduledTask,
  resumeScheduledTask,
  updateScheduledTask,
  type CreateScheduledTaskRequest,
  type ScheduledTaskWriteRequest,
} from './scheduled-tasks'


/**
 * 统一 Key 工厂：每个 Key 都带租户作用域，切换企业后缓存不串用；
 * 租户 ID 作为独立元素出现，App 层的 `queryKey.includes(previousWorkspaceId)`
 * 才能在切换工作区时精确清理本 Feature 的缓存。
 */
export const scheduledTaskKeys = {
  all: (tenantId: string) => ['scheduled-tasks', tenantId] as const,
  list: (tenantId: string, employeeId: string | undefined, offset: number) =>
    ['scheduled-tasks', tenantId, 'list', employeeId, offset] as const,
  detail: (tenantId: string, taskId: string) =>
    ['scheduled-tasks', tenantId, 'detail', taskId] as const,
  executions: (tenantId: string, taskId: string, offset: number) =>
    ['scheduled-tasks', tenantId, 'executions', taskId, offset] as const,
}

export function useScheduledTasks(employeeId?: string, offset = 0) {
  const tenantId = useActiveWorkspaceId()
  return useQuery({
    queryKey: scheduledTaskKeys.list(tenantId ?? '', employeeId, offset),
    queryFn: () => listScheduledTasks(tenantId!, { employeeId, offset }),
    enabled: Boolean(tenantId),
    refetchOnMount: 'always',
  })
}

export function useScheduledTask(taskId: string | undefined) {
  const tenantId = useActiveWorkspaceId()
  return useQuery({
    queryKey: scheduledTaskKeys.detail(tenantId ?? '', taskId ?? ''),
    queryFn: () => getScheduledTask(tenantId!, taskId!),
    enabled: Boolean(tenantId && taskId),
  })
}

export function useScheduledTaskExecutions(taskId: string | undefined, offset = 0) {
  const tenantId = useActiveWorkspaceId()
  return useQuery({
    queryKey: scheduledTaskKeys.executions(tenantId ?? '', taskId ?? '', offset),
    queryFn: () => listScheduledTaskExecutions(tenantId!, taskId!, { offset }),
    enabled: Boolean(tenantId && taskId),
    refetchOnMount: 'always',
  })
}

export function useCreateScheduledTask() {
  const tenantId = useActiveWorkspaceId()
  const queryClient = useQueryClient()
  return useMutation({
    mutationKey: tenantMutationKey(tenantId ?? '', 'scheduled-tasks', 'create'),
    mutationFn: (request: CreateScheduledTaskRequest) =>
      createScheduledTask(tenantId!, request),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: scheduledTaskKeys.all(tenantId ?? '') })
    },
  })
}

export function useUpdateScheduledTask(taskId: string) {
  const tenantId = useActiveWorkspaceId()
  const queryClient = useQueryClient()
  return useMutation({
    mutationKey: tenantMutationKey(tenantId ?? '', 'scheduled-tasks', taskId, 'update'),
    mutationFn: (request: ScheduledTaskWriteRequest) =>
      updateScheduledTask(tenantId!, taskId, request),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: scheduledTaskKeys.all(tenantId ?? '') })
    },
  })
}

export function usePauseScheduledTask() {
  const tenantId = useActiveWorkspaceId()
  const queryClient = useQueryClient()
  return useMutation({
    mutationKey: tenantMutationKey(tenantId ?? '', 'scheduled-tasks', 'pause'),
    mutationFn: (taskId: string) => pauseScheduledTask(tenantId!, taskId),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: scheduledTaskKeys.all(tenantId ?? '') })
    },
  })
}

export function useResumeScheduledTask() {
  const tenantId = useActiveWorkspaceId()
  const queryClient = useQueryClient()
  return useMutation({
    mutationKey: tenantMutationKey(tenantId ?? '', 'scheduled-tasks', 'resume'),
    mutationFn: (taskId: string) => resumeScheduledTask(tenantId!, taskId),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: scheduledTaskKeys.all(tenantId ?? '') })
    },
  })
}

export function useDeleteScheduledTask() {
  const tenantId = useActiveWorkspaceId()
  const queryClient = useQueryClient()
  return useMutation({
    mutationKey: tenantMutationKey(tenantId ?? '', 'scheduled-tasks', 'delete'),
    mutationFn: (taskId: string) => deleteScheduledTask(tenantId!, taskId),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: scheduledTaskKeys.all(tenantId ?? '') })
    },
  })
}
