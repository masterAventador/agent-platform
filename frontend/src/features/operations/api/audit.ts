import { z } from 'zod'

import { apiClient } from '../../../api/client'
import { tenantRequestConfig } from '../../../api/tenant'


const uuidSchema = z.uuid()
const dateTimeSchema = z.iso.datetime({ offset: true })
const auditMetadataSchema = z.record(z.string(), z.unknown())

const auditEventSchema = z.object({
  id: uuidSchema,
  tenant_id: uuidSchema,
  actor_user_id: uuidSchema.nullable(),
  sequence: z.number().int().positive(),
  action: z.string().min(1),
  resource_type: z.string().min(1),
  resource_id: z.string().nullable(),
  outcome: z.string().min(1),
  correlation_id: z.string().nullable(),
  previous_hash: z.string().length(64).nullable(),
  event_hash: z.string().length(64),
  metadata: auditMetadataSchema,
  occurred_at: dateTimeSchema,
}).strict()

const auditEventListSchema = z.array(auditEventSchema)

export type AuditEvent = z.infer<typeof auditEventSchema>

export async function listAuditEvents(tenantId: string, limit = 100): Promise<AuditEvent[]> {
  const response = await apiClient.get('/audit/events', {
    ...tenantRequestConfig(tenantId),
    params: { limit },
  })
  return auditEventListSchema.parse(response.data)
}
