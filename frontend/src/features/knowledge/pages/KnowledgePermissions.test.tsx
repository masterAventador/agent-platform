import { render, screen, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter, Route, Routes } from 'react-router-dom'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import { KnowledgeBaseDetailPage } from './KnowledgeBaseDetailPage'
import { KnowledgeBasesPage } from './KnowledgeBasesPage'


const deleteKnowledgeBase = vi.fn().mockResolvedValue(undefined)

vi.mock('../api/queries', () => ({
  useKnowledgeBases: vi.fn(() => ({
    data: [{
      id: 'knowledge-1',
      tenant_id: 'workspace-1',
      name: 'Policies',
      description: 'Company policies',
      provider: 'ragflow',
    }],
  })),
  useCreateKnowledgeBase: vi.fn(() => ({
    isPending: false,
    mutateAsync: vi.fn(),
  })),
  useDeleteKnowledgeBase: vi.fn(() => ({
    isPending: false,
    mutateAsync: deleteKnowledgeBase,
  })),
  useKnowledgeDocuments: vi.fn(() => ({ data: [] })),
  useUploadKnowledgeDocument: vi.fn(() => ({ isPending: false, mutate: vi.fn() })),
  useKnowledgeSearch: vi.fn(() => ({ isPending: false, mutate: vi.fn() })),
}))

function renderDetail(canManageKnowledge: boolean) {
  return render(
    <MemoryRouter initialEntries={['/knowledge-bases/knowledge-1']}>
      <Routes>
        <Route
          path="/knowledge-bases/:knowledgeBaseId"
          element={<KnowledgeBaseDetailPage canManageKnowledge={canManageKnowledge} />}
        />
        <Route path="/knowledge-bases" element={<div>知识库列表</div>} />
      </Routes>
    </MemoryRouter>,
  )
}

describe('knowledge capability controls', () => {
  beforeEach(() => vi.clearAllMocks())

  it('keeps read/search visible but hides every knowledge write control', () => {
    const { unmount } = render(
      <MemoryRouter>
        <KnowledgeBasesPage canManageKnowledge={false} />
      </MemoryRouter>,
    )
    expect(screen.getByText('Policies')).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: '创建知识库' })).not.toBeInTheDocument()
    unmount()

    renderDetail(false)
    expect(screen.getByRole('heading', { name: 'Policies' })).toBeInTheDocument()
    expect(screen.getByLabelText('检索问题')).toBeInTheDocument()
    expect(screen.queryByLabelText('选择文档')).not.toBeInTheDocument()
    expect(screen.queryByRole('button', { name: '上传并解析' })).not.toBeInTheDocument()
    expect(screen.queryByRole('button', { name: '删除知识库' })).not.toBeInTheDocument()
  })

  it('allows knowledge managers to delete after explicit confirmation', async () => {
    const user = userEvent.setup()
    renderDetail(true)

    await user.click(screen.getByRole('button', { name: '删除知识库' }))
    const dialog = await screen.findByRole('dialog', { name: '确认删除知识库' })
    await user.click(within(dialog).getByRole('button', { name: '确认删除' }))

    expect(deleteKnowledgeBase).toHaveBeenCalledWith('knowledge-1')
    expect(await screen.findByText('知识库列表')).toBeInTheDocument()
  })

  it('keeps the confirmation open and shows a visible error when deletion fails', async () => {
    const user = userEvent.setup()
    deleteKnowledgeBase.mockRejectedValueOnce(new Error('provider unavailable'))
    renderDetail(true)

    await user.click(screen.getByRole('button', { name: '删除知识库' }))
    const dialog = await screen.findByRole('dialog', { name: '确认删除知识库' })
    await user.click(within(dialog).getByRole('button', { name: '确认删除' }))

    expect(await within(dialog).findByText('知识库删除失败，请稍后重试')).toBeInTheDocument()
    expect(screen.queryByText('知识库列表')).not.toBeInTheDocument()
  })
})
