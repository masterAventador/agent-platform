import { z } from 'zod'

import { apiClient } from '../../../api/client'

const healthResponseSchema = z.object({
  status: z.literal('ok'),
})

export type HealthResponse = z.infer<typeof healthResponseSchema>

export async function getHealth(): Promise<HealthResponse> {
  const response = await apiClient.get<unknown>('/health/live')
  return healthResponseSchema.parse(response.data)
}
