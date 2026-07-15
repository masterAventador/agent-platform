import { z } from 'zod'

import { apiClient } from '../../../api/client'
import { tenantRequestConfig } from '../../../api/tenant'

const devicePlatformSchema = z.enum(['macos', 'windows'])
const deviceStatusSchema = z.enum(['online', 'offline', 'emergency_stopped'])
const accountGovernanceStatusSchema = z.enum([
  'awaiting_scan',
  'healthy',
  'human_handoff',
  'logged_out',
  'paused',
])
const accountActionResultSchema = z.enum([
  'succeeded',
  'failed',
  'abnormal_behavior',
])
const socialDeviceSchema = z.object({
  device_id: z.uuid(),
  tenant_id: z.uuid(),
  owner_user_id: z.uuid(),
  display_name: z.string().min(1).max(128),
  platform: devicePlatformSchema,
  app_version: z.string().min(1).max(128),
  executor_version: z.string().min(1).max(128),
  registered_at: z.iso.datetime({ offset: true }),
  last_seen_at: z.iso.datetime({ offset: true }),
  status: deviceStatusSchema,
  heartbeat_sequence: z.number().int().nonnegative(),
}).strict()

const socialDeviceListSchema = z.array(socialDeviceSchema)
const accountActionRecordSchema = z.object({
  account_id: z.uuid(),
  action_type: z.string().min(1).max(128),
  idempotency_key: z.string().min(1).max(128),
  result: accountActionResultSchema,
  occurred_at: z.iso.datetime({ offset: true }),
  consecutive_failures: z.number().int().nonnegative(),
}).strict()
const accountPolicyLimitSchema = z.object({
  action_type: z.string().min(1).max(128),
  daily_limit: z.number().int().positive(),
  effective_daily_limit: z.number().int().positive(),
  remaining_daily: z.number().int().nonnegative(),
  min_interval_seconds: z.number().int().nonnegative(),
  cold_start_days: z.number().int().nonnegative(),
  consecutive_failure_threshold: z.number().int().positive(),
  next_available_at: z.iso.datetime({ offset: true }).nullable(),
}).strict()
const socialAccountGovernanceSchema = z.object({
  account_id: z.uuid(),
  status: accountGovernanceStatusSchema,
  circuit_open: z.boolean(),
  health_score: z.number().int().min(0).max(100),
  recent_tasks: z.array(accountActionRecordSchema),
  failure_trend: z.record(z.string(), z.number().int().nonnegative()),
  policy_limits: z.record(z.string(), accountPolicyLimitSchema),
  recommendations: z.array(z.string().min(1).max(256)),
}).strict()
const accountActionAuthorizationSchema = z.object({
  account_id: z.uuid(),
  action_type: z.string().min(1).max(128),
  allowed: z.literal(true),
  remaining_daily: z.number().int().nonnegative(),
  next_available_at: z.iso.datetime({ offset: true }).nullable(),
  idempotency_key: z.string().min(1).max(128),
}).strict()

export type SocialDevice = z.infer<typeof socialDeviceSchema>
export type SocialDevicePlatform = z.infer<typeof devicePlatformSchema>
export type SocialAccountGovernance = z.infer<typeof socialAccountGovernanceSchema>
export type SocialAccountActionAuthorization = z.infer<
  typeof accountActionAuthorizationSchema
>

export interface RegisterSocialDeviceInput {
  device_id: string
  display_name: string
  platform: SocialDevicePlatform
  app_version: string
  executor_version: string
}

export async function registerSocialDevice(
  tenantId: string,
  input: RegisterSocialDeviceInput,
): Promise<SocialDevice> {
  const response = await apiClient.post(
    '/social-operations/devices/register',
    input,
    tenantRequestConfig(tenantId),
  )
  return socialDeviceSchema.parse(response.data)
}

export async function listSocialDevices(tenantId: string): Promise<SocialDevice[]> {
  const response = await apiClient.get(
    '/social-operations/devices',
    tenantRequestConfig(tenantId),
  )
  return socialDeviceListSchema.parse(response.data)
}

export async function getSocialAccountGovernance(
  tenantId: string,
  accountId: string,
): Promise<SocialAccountGovernance> {
  const response = await apiClient.get(
    `/social-operations/accounts/${accountId}/governance`,
    tenantRequestConfig(tenantId),
  )
  return socialAccountGovernanceSchema.parse(response.data)
}

export async function authorizeSocialAccountAction(
  tenantId: string,
  accountId: string,
  input: { action_type: string; idempotency_key: string },
): Promise<SocialAccountActionAuthorization> {
  const response = await apiClient.post(
    `/social-operations/accounts/${accountId}/actions/authorize`,
    {
      action_type: input.action_type,
      idempotency_key: input.idempotency_key,
    },
    tenantRequestConfig(tenantId),
  )
  return accountActionAuthorizationSchema.parse(response.data)
}

export async function pauseSocialAccount(
  tenantId: string,
  accountId: string,
  input: { reason: string },
): Promise<SocialAccountGovernance> {
  await apiClient.post(
    `/social-operations/accounts/${accountId}/pause`,
    { reason: input.reason },
    tenantRequestConfig(tenantId),
  )
  return getSocialAccountGovernance(tenantId, accountId)
}

export async function resumeSocialAccount(
  tenantId: string,
  accountId: string,
): Promise<SocialAccountGovernance> {
  await apiClient.post(
    `/social-operations/accounts/${accountId}/resume`,
    {},
    tenantRequestConfig(tenantId),
  )
  return getSocialAccountGovernance(tenantId, accountId)
}

export async function remoteStopSocialAccount(
  tenantId: string,
  accountId: string,
  input: { reason: string },
): Promise<SocialAccountGovernance> {
  await apiClient.post(
    `/social-operations/accounts/${accountId}/remote-stop`,
    { reason: input.reason },
    tenantRequestConfig(tenantId),
  )
  return getSocialAccountGovernance(tenantId, accountId)
}
