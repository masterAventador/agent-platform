import { beforeEach, describe, expect, expectTypeOf, it, vi } from 'vitest'

import { apiClient } from '../../../api/client'
import type {
  EmployeeDefinition,
  EmployeeWriteDefinition,
  GatewayModelReference,
} from './employees'
import { createEmployee, updateEmployee } from './employees'


vi.mock('../../../api/client', () => ({
  apiClient: {
    post: vi.fn(),
    put: vi.fn(),
  },
}))

const definition: EmployeeWriteDefinition = {
  name: '研究专员',
  avatar_url: null,
  role_description: '负责研究任务',
  visibility: 'tenant',
  work_mode: 'autonomous',
  system_prompt: '完成用户交付的研究任务',
  model: { kind: 'gateway_alias', alias: 'general-purpose' },
  input_schema: { type: 'object' },
  output_schema: { type: 'object' },
  capabilities: {
    conversation: true,
    scheduled_tasks: false,
    file_upload: false,
  },
  skill_ids: [],
  tool_ids: [],
  knowledge_base_ids: [],
  approval_policy: {},
  release_strategy: { mode: 'all' },
}

describe('employee write boundary', () => {
  beforeEach(() => {
    vi.clearAllMocks()
  })

  it('keeps legacy read definitions wider than the supported write contract', () => {
    expectTypeOf<EmployeeWriteDefinition>().toExtend<EmployeeDefinition>()
    expectTypeOf<EmployeeDefinition>().not.toExtend<EmployeeWriteDefinition>()
    expectTypeOf<GatewayModelReference['alias']>().toEqualTypeOf<string>()
  })

  it('keeps the API model reference wide enough for configured backend aliases', () => {
    const configuredModel: GatewayModelReference = {
      kind: 'gateway_alias',
      alias: 'future-configured-model',
    }

    expect(configuredModel.alias).toBe('future-configured-model')
  })

  it.each([
    ['workflow mode', { ...definition, work_mode: 'workflow' as const } as EmployeeDefinition],
    ['hybrid mode', { ...definition, work_mode: 'hybrid' as const } as EmployeeDefinition],
    [
      'scheduled tasks',
      { ...definition, capabilities: { ...definition.capabilities, scheduled_tasks: true } },
    ],
  ])('rejects unavailable %s before create reaches the API', async (_, unavailable) => {
    await expect(createEmployee(
      'tenant-1',
      unavailable as unknown as EmployeeWriteDefinition,
    )).rejects.toThrow(
      'employee_configuration_unavailable',
    )
    expect(apiClient.post).not.toHaveBeenCalled()
  })

  it.each([
    ['workflow mode', { ...definition, work_mode: 'workflow' as const } as EmployeeDefinition],
    ['hybrid mode', { ...definition, work_mode: 'hybrid' as const } as EmployeeDefinition],
    [
      'scheduled tasks',
      { ...definition, capabilities: { ...definition.capabilities, scheduled_tasks: true } },
    ],
  ])('rejects unavailable %s before update reaches the API', async (_, unavailable) => {
    await expect(updateEmployee(
      'tenant-1',
      'employee-1',
      unavailable as unknown as EmployeeWriteDefinition,
    )).rejects.toThrow(
      'employee_configuration_unavailable',
    )
    expect(apiClient.put).not.toHaveBeenCalled()
  })

  it('allows file upload capability through the write boundary', async () => {
    vi.mocked(apiClient.post).mockResolvedValue({ data: { id: 'employee-1' } })
    const withFiles = {
      ...definition,
      capabilities: { ...definition.capabilities, file_upload: true },
    }

    await createEmployee('tenant-1', withFiles)

    expect(apiClient.post).toHaveBeenCalledWith('/employees', withFiles, expect.anything())
  })
})
