import { apiClient } from '../../../api/client'
import { tenantRequestConfig } from '../../../api/tenant'
import { EmployeeConfigurationUnavailableError } from './errors'


export type WorkMode = 'autonomous' | 'workflow' | 'hybrid'
export type EmployeeStatus = 'draft' | 'published'

export interface EmployeeCapabilities {
  conversation: boolean
  scheduled_tasks: boolean
  file_upload: boolean
}

export interface EmployeeWriteCapabilities {
  conversation: boolean
  scheduled_tasks: false
  file_upload: false
}

export interface GatewayModelReference {
  kind: 'gateway_alias'
  alias: string
}

export interface EmployeeDefinition {
  name: string
  avatar_url?: string | null
  role_description: string
  visibility: 'private' | 'tenant'
  work_mode: WorkMode
  system_prompt: string
  model: GatewayModelReference
  input_schema: Record<string, unknown>
  output_schema: Record<string, unknown>
  capabilities: EmployeeCapabilities
  skill_ids: string[]
  tool_ids: string[]
  knowledge_base_ids: string[]
  approval_policy: Record<string, unknown>
  release_strategy: Record<string, unknown>
}

export type EmployeeWriteDefinition = Omit<EmployeeDefinition, 'work_mode' | 'capabilities'> & {
  work_mode: 'autonomous'
  capabilities: EmployeeWriteCapabilities
}

export interface Employee {
  id: string
  tenant_id: string
  name: string
  status: EmployeeStatus
  published_version: number | null
  definition: EmployeeDefinition
}

export function isEmployeeConfigurationAvailable(definition: EmployeeDefinition): boolean {
  return definition.work_mode === 'autonomous'
    && !definition.capabilities.scheduled_tasks
    && !definition.capabilities.file_upload
}

function assertEmployeeConfigurationAvailable(definition: EmployeeDefinition): void {
  if (!isEmployeeConfigurationAvailable(definition)) {
    throw new EmployeeConfigurationUnavailableError()
  }
}

export async function listEmployees(tenantId: string): Promise<Employee[]> {
  const response = await apiClient.get<Employee[]>('/employees', {
    ...tenantRequestConfig(tenantId),
  })
  return response.data
}

export async function getEmployee(tenantId: string, employeeId: string): Promise<Employee> {
  const response = await apiClient.get<Employee>(`/employees/${employeeId}`, {
    ...tenantRequestConfig(tenantId),
  })
  return response.data
}

export async function createEmployee(
  tenantId: string,
  definition: EmployeeWriteDefinition,
): Promise<Employee> {
  assertEmployeeConfigurationAvailable(definition)
  const response = await apiClient.post<Employee>('/employees', definition, {
    ...tenantRequestConfig(tenantId),
  })
  return response.data
}

export async function updateEmployee(
  tenantId: string,
  employeeId: string,
  definition: EmployeeWriteDefinition,
): Promise<Employee> {
  assertEmployeeConfigurationAvailable(definition)
  const response = await apiClient.put<Employee>(`/employees/${employeeId}`, definition, {
    ...tenantRequestConfig(tenantId),
  })
  return response.data
}

export async function publishEmployee(
  tenantId: string,
  employeeId: string,
): Promise<Employee> {
  const response = await apiClient.post<Employee>(
    `/employees/${employeeId}/publish`,
    undefined,
    tenantRequestConfig(tenantId),
  )
  return response.data
}
