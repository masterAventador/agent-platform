import { Result } from 'antd'
import type { ReactNode } from 'react'

import { hasWorkspacePermission } from '../permissions'
import type { WorkspacePermission } from '../permissions'
import type { Workspace } from '../types'


interface WorkspaceCapabilityGateProps {
  workspace: Workspace
  permission: WorkspacePermission
  title: string
  children: ReactNode
}

export function WorkspaceCapabilityGate({
  workspace,
  permission,
  title,
  children,
}: WorkspaceCapabilityGateProps) {
  if (hasWorkspacePermission(workspace, permission)) return children

  return (
    <Result
      status="403"
      title={title}
      subTitle="当前工作区没有执行此操作的权限，请联系工作区所有者。"
    />
  )
}
