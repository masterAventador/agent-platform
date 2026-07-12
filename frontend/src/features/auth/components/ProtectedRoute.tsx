import { Flex, Spin } from 'antd'
import type { ReactNode } from 'react'
import { Navigate, useLocation } from 'react-router-dom'

import { useCurrentUser } from '../api/queries'

export function ProtectedRoute({ children }: { children: ReactNode }) {
  const currentUser = useCurrentUser()
  const location = useLocation()

  if (currentUser.isPending) {
    return (
      <Flex className="auth-loading" align="center" justify="center">
        <Spin size="large" aria-label="正在恢复登录状态" />
      </Flex>
    )
  }

  if (!currentUser.data) {
    return <Navigate to="/login" replace state={{ from: location.pathname }} />
  }

  return children
}
