import { beforeEach, describe, expect, it, vi } from 'vitest'

import { apiClient } from '../../../api/client'
import {
  addWorkflowVersion,
  listWorkflows,
  listWorkflowVersions,
  publishedWorkflowOptions,
  publishWorkflow,
  registerWorkflow,
  rollbackWorkflow,
  type Workflow,
} from './workflows'


vi.mock('../../../api/client', () => ({
  apiClient: {
    get: vi.fn(),
    post: vi.fn(),
  },
}))

const graph = {
  entrypoint: 'a',
  nodes: [{ name: 'a', type: 'agent', config: { prompt: 'hi' }, next: null }],
}

describe('workflows api client', () => {
  beforeEach(() => {
    vi.clearAllMocks()
  })

  it('registers a workflow with graph and description', async () => {
    vi.mocked(apiClient.post).mockResolvedValue({ data: { id: 'w1' } })
    await registerWorkflow('tenant-1', { name: '客服', description: 'd', graph })
    expect(apiClient.post).toHaveBeenCalledWith(
      '/workflows',
      { name: '客服', description: 'd', graph },
      expect.objectContaining({ headers: expect.objectContaining({ 'X-Tenant-ID': 'tenant-1' }) }),
    )
  })

  it('lists workflows for the tenant', async () => {
    vi.mocked(apiClient.get).mockResolvedValue({ data: [] })
    await listWorkflows('tenant-1')
    expect(apiClient.get).toHaveBeenCalledWith(
      '/workflows',
      expect.objectContaining({ headers: expect.objectContaining({ 'X-Tenant-ID': 'tenant-1' }) }),
    )
  })

  it('adds a version', async () => {
    vi.mocked(apiClient.post).mockResolvedValue({ data: { id: 'w1' } })
    await addWorkflowVersion('tenant-1', 'w1', { description: 'v2', graph })
    expect(apiClient.post).toHaveBeenCalledWith(
      '/workflows/w1/versions',
      { description: 'v2', graph },
      expect.anything(),
    )
  })

  it('publishes and rolls back by version', async () => {
    vi.mocked(apiClient.post).mockResolvedValue({ data: { id: 'w1' } })
    await publishWorkflow('tenant-1', 'w1', 2)
    expect(apiClient.post).toHaveBeenCalledWith(
      '/workflows/w1/publish',
      { version: 2 },
      expect.anything(),
    )
    await rollbackWorkflow('tenant-1', 'w1', 1)
    expect(apiClient.post).toHaveBeenCalledWith(
      '/workflows/w1/rollback',
      { version: 1 },
      expect.anything(),
    )
  })

  it('lists versions', async () => {
    vi.mocked(apiClient.get).mockResolvedValue({ data: [] })
    await listWorkflowVersions('tenant-1', 'w1')
    expect(apiClient.get).toHaveBeenCalledWith('/workflows/w1/versions', expect.anything())
  })

  it('exposes only published workflows as reference options', () => {
    const workflows: Workflow[] = [
      {
        id: 'w1',
        tenant_id: 't',
        name: '已发布',
        description: '',
        latest_version: 2,
        published_version: 2,
        status: 'published',
      },
      {
        id: 'w2',
        tenant_id: 't',
        name: '草稿',
        description: '',
        latest_version: 1,
        published_version: null,
        status: 'draft',
      },
    ]
    const options = publishedWorkflowOptions(workflows)
    expect(options).toEqual([{ value: 'w1', label: '已发布（v2）' }])
  })
})
