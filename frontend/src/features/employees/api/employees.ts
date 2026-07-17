import { apiClient } from '../../../api/client'
import { tenantRequestConfig } from '../../../api/tenant'
import { EmployeeConfigurationUnavailableError } from './errors'


export type WorkMode = 'autonomous' | 'workflow' | 'hybrid'
export type EmployeeStatus = 'draft' | 'published'

export interface EmployeeCapabilities {
  conversation: boolean
  scheduled_tasks: boolean
  file_upload: boolean
  memory?: boolean
}

export interface EmployeeWriteCapabilities {
  conversation: boolean
  scheduled_tasks: false
  file_upload: boolean
  memory: boolean
}

export interface GatewayModelReference {
  kind: 'gateway_alias'
  alias: string
}

export type KnowledgeMetadataComparisonOperator =
  | 'contains'
  | 'not contains'
  | 'start with'
  | 'empty'
  | 'not empty'
  | '='
  | '≠'
  | '>'
  | '<'
  | '≥'
  | '≤'

export interface KnowledgeMetadataFilterCondition {
  name: string
  comparison_operator: KnowledgeMetadataComparisonOperator
  value: string
}

export interface KnowledgeMetadataCondition {
  logic: 'and' | 'or'
  conditions: KnowledgeMetadataFilterCondition[]
}

/** 字段名对齐 RAGFlow v0.25.6 官方检索 API；后端返回时总是包含全部字段。 */
export interface KnowledgeRetrievalConfig {
  page_size: number
  similarity_threshold: number
  vector_similarity_weight: number
  top_k: number
  keyword: boolean
  rerank_id: string | null
  metadata_condition: KnowledgeMetadataCondition | null
}

export const defaultKnowledgeRetrievalConfig: KnowledgeRetrievalConfig = {
  page_size: 5,
  similarity_threshold: 0.2,
  vector_similarity_weight: 0.3,
  top_k: 1024,
  keyword: false,
  rerank_id: null,
  metadata_condition: null,
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
  knowledge_retrieval: KnowledgeRetrievalConfig
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
