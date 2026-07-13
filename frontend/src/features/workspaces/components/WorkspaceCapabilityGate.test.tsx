import { render, screen } from '@testing-library/react'
import { describe, expect, it } from 'vitest'

import type { Workspace } from '../types'
import { workspacePermissions } from '../permissions'
import { WorkspaceCapabilityGate } from './WorkspaceCapabilityGate'


const workspace: Workspace = {
  id: 'workspace-1',
  name: 'Workspace',
  slug: 'workspace',
  role: 'owner',
  permissions: [],
}

describe('WorkspaceCapabilityGate', () => {
  it('renders one consistent 403 without inferring access from role', () => {
    render(
      <WorkspaceCapabilityGate
        workspace={workspace}
        permission={workspacePermissions.employeesManage}
        title="无权编辑数字员工"
      >
        <div>员工编辑器</div>
      </WorkspaceCapabilityGate>,
    )

    expect(screen.getByText('无权编辑数字员工')).toBeInTheDocument()
    expect(screen.getByText(
      '当前工作区没有执行此操作的权限，请联系工作区所有者。',
    )).toBeInTheDocument()
    expect(screen.queryByText('员工编辑器')).not.toBeInTheDocument()
  })

  it('renders children when the backend permission is present', () => {
    render(
      <WorkspaceCapabilityGate
        workspace={{ ...workspace, role: 'member', permissions: ['employees.manage'] }}
        permission={workspacePermissions.employeesManage}
        title="无权编辑数字员工"
      >
        <div>员工编辑器</div>
      </WorkspaceCapabilityGate>,
    )

    expect(screen.getByText('员工编辑器')).toBeInTheDocument()
    expect(screen.queryByText('无权编辑数字员工')).not.toBeInTheDocument()
  })
})
