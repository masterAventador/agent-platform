import { render, screen } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import { EmployeesPage } from '../employees/pages/EmployeesPage'
import { SkillDetailPage } from '../skills/pages/SkillDetailPage'
import { SkillsPage } from '../skills/pages/SkillsPage'
import { ToolsPage } from '../tools/pages/ToolsPage'


vi.mock('../employees/api/queries', () => ({
  useEmployees: vi.fn(() => ({ data: [], isPending: false })),
}))

vi.mock('../skills/api/queries', () => ({
  useSkills: vi.fn(() => ({ data: [], isPending: false })),
  useCreateSkill: vi.fn(() => ({ isPending: false, isError: false, reset: vi.fn() })),
  useSkill: vi.fn(() => ({
    isPending: false,
    data: {
      id: 'skill-1',
      name: 'Research',
      description: 'readable skill',
      status: 'draft',
      latest_version: 1,
      published_version: null,
      source: 'uploaded',
    },
  })),
  useSkillVersions: vi.fn(() => ({
    isPending: false,
    data: [{
      version: 1,
      description: 'first',
      digest: 'a'.repeat(64),
      files: ['SKILL.md'],
      review_status: 'approved',
      security_findings: [],
      created_at: '2026-07-16T00:00:00Z',
      reviewed_at: '2026-07-16T00:00:00Z',
      published_at: null,
    }],
  })),
  useSkillFile: vi.fn(() => ({ isPending: false, data: '# Skill' })),
  useAddSkillVersion: vi.fn(() => ({ isPending: false, isError: false, reset: vi.fn() })),
  usePublishSkillVersion: vi.fn(() => ({ isPending: false, mutate: vi.fn() })),
  useSkillVersionDiff: vi.fn(() => ({ data: { added: [], removed: [], changed: [] } })),
  useSkillUsage: vi.fn(() => ({ data: { items: [] } })),
  useOfflineSkill: vi.fn(() => ({ isPending: false, mutate: vi.fn() })),
  useDeleteSkill: vi.fn(() => ({ isPending: false, mutate: vi.fn() })),
}))

vi.mock('../tools/api/queries', () => ({
  useMcpServers: vi.fn(() => ({
    data: [{
      id: 'server-1',
      name: 'Server One',
      transport: 'streamable_http',
      endpoint: 'https://mcp.example.com',
      command: null,
      args: [],
      has_credentials: false,
      enabled: true,
    }],
  })),
  useTools: vi.fn(() => ({
    data: [{
      id: 'tool-1',
      server_id: 'server-1',
      name: 'Search',
      description: 'read only search',
      risk_level: 'read',
      enabled: true,
    }],
  })),
  useCreateMcpServer: vi.fn(() => ({ isPending: false, isError: false, reset: vi.fn() })),
  useCreateTool: vi.fn(() => ({ isPending: false, isError: false, reset: vi.fn() })),
  useSetMcpServerEnabled: vi.fn(() => ({ isPending: false, mutate: vi.fn() })),
  useSetToolEnabled: vi.fn(() => ({ isPending: false, mutate: vi.fn() })),
}))

describe('workspace action-specific write permissions', () => {
  beforeEach(() => vi.clearAllMocks())

  it('without employees.manage the employee list stays read-only', () => {
    render(<MemoryRouter><EmployeesPage canManageEmployees={false} /></MemoryRouter>)

    expect(screen.getByRole('heading', { name: '数字员工' })).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: '创建数字员工' })).not.toBeInTheDocument()
  })

  it('without skills.manage the skill pages show no write controls', () => {
    const { unmount } = render(
      <MemoryRouter><SkillsPage canManageSkills={false} /></MemoryRouter>,
    )
    expect(screen.getByRole('heading', { name: 'Skill 中心' })).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: '上传 Skill' })).not.toBeInTheDocument()
    unmount()

    render(<MemoryRouter><SkillDetailPage canManageSkills={false} /></MemoryRouter>)
    expect(screen.getByText('readable skill')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: '查看版本 1' })).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: '上传新版本' })).not.toBeInTheDocument()
    expect(screen.queryByRole('button', { name: '发布版本 1' })).not.toBeInTheDocument()
  })

  it('without tools.manage the registry component exposes no mutations', () => {
    render(<ToolsPage canManageTools={false} />)

    expect(screen.getAllByText('Server One').length).toBeGreaterThan(0)
    expect(screen.getByText('Search')).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: '注册 MCP Server' })).not.toBeInTheDocument()
    expect(screen.queryByRole('button', { name: '登记 Tool' })).not.toBeInTheDocument()
    expect(screen.queryByRole('button', { name: '禁用' })).not.toBeInTheDocument()
    expect(screen.queryByRole('button', { name: '启用' })).not.toBeInTheDocument()
  })

  it('does not couple one feature grant to another feature write surface', () => {
    const { unmount } = render(
      <MemoryRouter><SkillsPage canManageSkills /></MemoryRouter>,
    )
    expect(screen.getByRole('button', { name: '上传 Skill' })).toBeInTheDocument()
    unmount()

    render(<ToolsPage canManageTools={false} />)
    expect(screen.queryByRole('button', { name: '注册 MCP Server' })).not.toBeInTheDocument()
  })
})
