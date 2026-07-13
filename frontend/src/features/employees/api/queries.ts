import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'

import { tenantMutationKey } from '../../../api/tenant'
import { useActiveWorkspaceId } from '../../workspaces/store'
import {
  createEmployee,
  getEmployee,
  listEmployees,
  publishEmployee,
  updateEmployee,
  type EmployeeWriteDefinition,
} from './employees'


export const employeeKeys = {
  all: (tenantId: string) => ['employees', tenantId] as const,
  detail: (tenantId: string, employeeId: string) =>
    ['employees', tenantId, employeeId] as const,
}

export function useEmployees() {
  const tenantId = useActiveWorkspaceId()
  return useQuery({
    queryKey: employeeKeys.all(tenantId ?? ''),
    queryFn: () => listEmployees(tenantId!),
    enabled: Boolean(tenantId),
  })
}

export function useEmployee(employeeId: string | undefined) {
  const tenantId = useActiveWorkspaceId()
  return useQuery({
    queryKey: employeeKeys.detail(tenantId ?? '', employeeId ?? ''),
    queryFn: () => getEmployee(tenantId!, employeeId!),
    enabled: Boolean(tenantId && employeeId),
  })
}

export function useCreateEmployee() {
  const tenantId = useActiveWorkspaceId()
  const queryClient = useQueryClient()
  return useMutation({
    mutationKey: tenantMutationKey(tenantId ?? '', 'employees', 'create'),
    mutationFn: (definition: EmployeeWriteDefinition) => createEmployee(tenantId!, definition),
    onSuccess: async () => {
      await queryClient.invalidateQueries({ queryKey: employeeKeys.all(tenantId!) })
    },
  })
}

export function useUpdateEmployee(employeeId: string) {
  const tenantId = useActiveWorkspaceId()
  const queryClient = useQueryClient()
  return useMutation({
    mutationKey: tenantMutationKey(tenantId ?? '', 'employees', 'update', employeeId),
    mutationFn: (definition: EmployeeWriteDefinition) =>
      updateEmployee(tenantId!, employeeId, definition),
    onSuccess: async (employee) => {
      queryClient.setQueryData(employeeKeys.detail(tenantId!, employeeId), employee)
      await queryClient.invalidateQueries({ queryKey: employeeKeys.all(tenantId!) })
    },
  })
}

export function usePublishEmployee(employeeId: string) {
  const tenantId = useActiveWorkspaceId()
  const queryClient = useQueryClient()
  return useMutation({
    mutationKey: tenantMutationKey(tenantId ?? '', 'employees', 'publish', employeeId),
    mutationFn: () => publishEmployee(tenantId!, employeeId),
    onSuccess: async (employee) => {
      queryClient.setQueryData(employeeKeys.detail(tenantId!, employeeId), employee)
      await queryClient.invalidateQueries({ queryKey: employeeKeys.all(tenantId!) })
    },
  })
}
