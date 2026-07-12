import { useQuery } from '@tanstack/react-query'
import { Alert } from 'antd'

import { getHealth } from '../api/health'

const HEALTH_QUERY_KEY = ['system', 'health'] as const

export function BackendStatus() {
  const healthQuery = useQuery({
    queryKey: HEALTH_QUERY_KEY,
    queryFn: getHealth,
  })

  if (healthQuery.isPending) {
    return <Alert title="正在连接后端服务" showIcon type="info" />
  }

  if (healthQuery.isError) {
    return <Alert title="后端服务暂不可用" showIcon type="error" />
  }

  return <Alert title="后端服务正常" showIcon type="success" />
}
