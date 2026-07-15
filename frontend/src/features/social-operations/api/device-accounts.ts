import { z } from 'zod'

import { apiClient } from '../../../api/client'
import { tenantRequestConfig } from '../../../api/tenant'

const devicePlatformSchema = z.enum(['macos', 'windows'])
const deviceStatusSchema = z.enum(['online', 'offline', 'emergency_stopped'])
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

export type SocialDevice = z.infer<typeof socialDeviceSchema>
export type SocialDevicePlatform = z.infer<typeof devicePlatformSchema>

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
