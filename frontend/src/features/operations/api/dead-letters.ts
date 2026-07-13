import { z } from 'zod'

import { apiClient } from '../../../api/client'
import { tenantRequestConfig } from '../../../api/tenant'


const uuidSchema = z.uuid()
const dateTimeSchema = z.iso.datetime({ offset: true })
const nullableDateTimeSchema = dateTimeSchema.nullable()
const sha256Schema = z.string().regex(/^[0-9a-f]{64}$/)
const knownQueueFieldSchema = z.enum([
  'command_id',
  'run_id',
  'tenant_id',
  'action',
  'payload',
])
const rawFieldFingerprintSchema = z.object({
  length: z.number().int().nonnegative(),
  sha256: sha256Schema,
}).strict()
const rawFieldsSummarySchema = z.object({
  known_field_keys: z.array(knownQueueFieldSchema).max(5),
  unknown_fields: z.array(rawFieldFingerprintSchema).max(32),
  field_count: z.number().int().nonnegative(),
  total_bytes: z.number().int().nonnegative(),
  sha256: sha256Schema.nullable(),
}).strict()

const runDeadLetterSchema = z.object({
  id: uuidSchema,
  original_command_id: uuidSchema.nullable(),
  original_run_id: uuidSchema.nullable(),
  action: z.string().nullable(),
  attempts: z.number().int().nonnegative(),
  error_type: z.string(),
  is_malformed: z.boolean(),
  raw_fields_summary: rawFieldsSummarySchema,
  failed_at: dateTimeSchema,
  settled_run_id: uuidSchema.nullable(),
  replayed_run_id: uuidSchema.nullable(),
  replayed_command_id: uuidSchema.nullable(),
  replayed_at: nullableDateTimeSchema,
  mirrored_at: nullableDateTimeSchema,
}).strict()

const replayedRunSchema = z.object({
  run_id: uuidSchema,
  command_id: uuidSchema,
}).strict()

const runDeadLetterListSchema = z.array(runDeadLetterSchema)

export type RunDeadLetter = z.infer<typeof runDeadLetterSchema>
export type ReplayedRun = z.infer<typeof replayedRunSchema>

export async function listRunDeadLetters(
  tenantId: string,
  limit = 100,
): Promise<RunDeadLetter[]> {
  const response = await apiClient.get('/run-dead-letters', {
    ...tenantRequestConfig(tenantId),
    params: { limit },
  })
  return runDeadLetterListSchema.parse(response.data)
}

export async function replayRunDeadLetter(
  tenantId: string,
  deadLetterId: string,
): Promise<ReplayedRun> {
  const response = await apiClient.post(
    `/run-dead-letters/${deadLetterId}/replay`,
    undefined,
    tenantRequestConfig(tenantId),
  )
  return replayedRunSchema.parse(response.data)
}
