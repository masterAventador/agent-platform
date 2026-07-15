import { z } from 'zod'

import { apiClient } from '../../../api/client'
import { tenantRequestConfig } from '../../../api/tenant'


const countSchema = z.number().int().nonnegative()
const workbenchSummarySchema = z.strictObject({
  employees: z.strictObject({
    total: countSchema,
    draft: countSchema,
    published: countSchema,
  }),
  runs: z.strictObject({
    total: countSchema,
    queued: countSchema,
    running: countSchema,
    waiting_for_input: countSchema,
    waiting_for_approval: countSchema,
    completed: countSchema,
    failed: countSchema,
    cancelled: countSchema,
  }),
})

export type WorkbenchSummary = z.infer<typeof workbenchSummarySchema>

export async function getWorkbenchSummary(tenantId: string): Promise<WorkbenchSummary> {
  const response = await apiClient.get<unknown>(
    '/workbench/summary',
    tenantRequestConfig(tenantId),
  )
  return workbenchSummarySchema.parse(response.data)
}
