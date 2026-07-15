import { fireEvent, render, screen } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import { describe, expect, it, vi } from 'vitest'

import { SkillDetailPage } from './SkillDetailPage'

const offlineMutate = vi.fn()
const deleteMutate = vi.fn()

vi.mock('../api/queries', () => ({
  useSkill: vi.fn(() => ({
    isPending: false,
    data: {
      id: 'skill-1',
      tenant_id: 'tenant-1',
      name: 'report-writer',
      description: 'Create better reports.',
      status: 'published',
      latest_version: 2,
      published_version: 2,
    },
  })),
  useSkillVersions: vi.fn(() => ({
    isPending: false,
    data: [
      {
        version: 2,
        description: 'Create better reports.',
        digest: 'b'.repeat(64),
        files: ['SKILL.md', 'references/guide.md'],
        created_at: '2026-07-16T00:00:00Z',
        published_at: '2026-07-16T00:00:00Z',
        review_status: 'approved',
        security_findings: [
          {
            severity: 'info',
            category: 'archive',
            code: 'archive_scanned',
            message: 'ZIP 包结构已审核',
            path: null,
          },
          {
            severity: 'warning',
            category: 'script',
            code: 'script_present',
            message: '包含脚本文件，运行时必须进入沙箱',
            path: 'scripts/run.py',
          },
        ],
      },
      {
        version: 1,
        description: 'Create reports.',
        digest: 'a'.repeat(64),
        files: ['SKILL.md', 'references/guide.md'],
        created_at: '2026-07-15T00:00:00Z',
        published_at: '2026-07-15T00:00:00Z',
        review_status: 'approved',
        security_findings: [],
      },
    ],
  })),
  useSkillFile: vi.fn(() => ({ isPending: false, data: '# Report writer' })),
  useAddSkillVersion: vi.fn(() => ({ isPending: false, isError: false, reset: vi.fn() })),
  usePublishSkillVersion: vi.fn(() => ({ isPending: false, mutate: vi.fn() })),
  useSkillVersionDiff: vi.fn(() => ({
    data: {
      from_version: 1,
      to_version: 2,
      added: [],
      removed: [],
      changed: ['references/guide.md'],
    },
  })),
  useSkillUsage: vi.fn(() => ({
    data: {
      items: [
        {
          employee_id: 'employee-1',
          employee_name: '生命周期报告专员',
          relation: 'employee_version',
          version: 1,
        },
      ],
    },
  })),
  useOfflineSkill: vi.fn(() => ({ isPending: false, mutate: offlineMutate })),
  useDeleteSkill: vi.fn(() => ({ isPending: false, mutate: deleteMutate })),
}))

describe('SkillDetailPage lifecycle panels', () => {
  it('shows security review, version diff, usage relations and lifecycle actions', () => {
    render(
      <MemoryRouter>
        <SkillDetailPage canManageSkills />
      </MemoryRouter>,
    )

    expect(screen.getByRole('heading', { name: '安全审核结果' })).toBeInTheDocument()
    expect(screen.getByText('ZIP 包结构已审核')).toBeInTheDocument()
    expect(screen.getByText('scripts/run.py')).toBeInTheDocument()
    expect(screen.getByRole('heading', { name: '版本差异' })).toBeInTheDocument()
    expect(screen.getAllByText('references/guide.md').length).toBeGreaterThan(0)
    expect(screen.getByRole('heading', { name: '使用关系' })).toBeInTheDocument()
    expect(screen.getByText(/生命周期报告专员/)).toBeInTheDocument()
    expect(screen.getByRole('button', { name: '下线 Skill' })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: '删除 Skill' })).toBeInTheDocument()

    fireEvent.click(screen.getByRole('button', { name: '下线 Skill' }))
    expect(offlineMutate).toHaveBeenCalledWith()
  })

  it('hides lifecycle mutations when the user cannot manage skills', () => {
    render(
      <MemoryRouter>
        <SkillDetailPage canManageSkills={false} />
      </MemoryRouter>,
    )

    expect(screen.queryByRole('button', { name: '下线 Skill' })).not.toBeInTheDocument()
    expect(screen.queryByRole('button', { name: '删除 Skill' })).not.toBeInTheDocument()
  })
})
