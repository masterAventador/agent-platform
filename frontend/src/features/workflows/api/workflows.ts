import { apiClient } from '../../../api/client'
import { tenantRequestConfig } from '../../../api/tenant'


export type WorkflowStatus = 'draft' | 'published'

/** 工作流图为平台自研协议对象；前端按不透明结构透传，不解释 LangGraph 内部。 */
export type WorkflowGraph = Record<string, unknown>

export interface Workflow {
  id: string
  tenant_id: string
  name: string
  description: string
  latest_version: number
  published_version: number | null
  status: WorkflowStatus
}

export interface WorkflowVersion {
  version: number
  description: string
  graph: WorkflowGraph
  created_at: string
  published_at: string | null
}

export interface RegisterWorkflowRequest {
  name: string
  description: string
  graph: WorkflowGraph
}

export interface AddWorkflowVersionRequest {
  description: string
  graph: WorkflowGraph
}

export interface WorkflowReferenceOption {
  value: string
  label: string
}

export async function listWorkflows(tenantId: string): Promise<Workflow[]> {
  const response = await apiClient.get<Workflow[]>('/workflows', tenantRequestConfig(tenantId))
  return response.data
}

export async function getWorkflow(tenantId: string, workflowId: string): Promise<Workflow> {
  const response = await apiClient.get<Workflow>(
    `/workflows/${workflowId}`,
    tenantRequestConfig(tenantId),
  )
  return response.data
}

export async function registerWorkflow(
  tenantId: string,
  request: RegisterWorkflowRequest,
): Promise<Workflow> {
  const response = await apiClient.post<Workflow>(
    '/workflows',
    request,
    tenantRequestConfig(tenantId),
  )
  return response.data
}

export async function addWorkflowVersion(
  tenantId: string,
  workflowId: string,
  request: AddWorkflowVersionRequest,
): Promise<Workflow> {
  const response = await apiClient.post<Workflow>(
    `/workflows/${workflowId}/versions`,
    request,
    tenantRequestConfig(tenantId),
  )
  return response.data
}

export async function listWorkflowVersions(
  tenantId: string,
  workflowId: string,
): Promise<WorkflowVersion[]> {
  const response = await apiClient.get<WorkflowVersion[]>(
    `/workflows/${workflowId}/versions`,
    tenantRequestConfig(tenantId),
  )
  return response.data
}

export async function publishWorkflow(
  tenantId: string,
  workflowId: string,
  version: number,
): Promise<Workflow> {
  const response = await apiClient.post<Workflow>(
    `/workflows/${workflowId}/publish`,
    { version },
    tenantRequestConfig(tenantId),
  )
  return response.data
}

export async function rollbackWorkflow(
  tenantId: string,
  workflowId: string,
  version: number,
): Promise<Workflow> {
  const response = await apiClient.post<Workflow>(
    `/workflows/${workflowId}/rollback`,
    { version },
    tenantRequestConfig(tenantId),
  )
  return response.data
}

/** 员工编辑器只能引用已发布工作流。 */
export function publishedWorkflowOptions(workflows: Workflow[]): WorkflowReferenceOption[] {
  return workflows
    .filter((workflow) => workflow.published_version !== null)
    .map((workflow) => ({
      value: workflow.id,
      label: `${workflow.name}（v${workflow.published_version}）`,
    }))
}
