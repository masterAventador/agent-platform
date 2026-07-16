import { render, screen, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter, Route, Routes } from 'react-router-dom'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import {
  useDeleteKnowledgeDocument,
  useKnowledgeDocuments,
  useReplaceKnowledgeDocument,
  useRetryKnowledgeDocument,
  useUploadKnowledgeDocuments,
} from '../api/queries'
import { KnowledgeBaseDetailPage } from './KnowledgeBaseDetailPage'
import { KnowledgeBasesPage } from './KnowledgeBasesPage'


const deleteKnowledgeBase = vi.fn().mockResolvedValue(undefined)
const uploadKnowledgeDocuments = vi.fn()
const retryKnowledgeDocument = vi.fn()
const replaceKnowledgeDocument = vi.fn()
const deleteKnowledgeDocument = vi.fn()

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
  useUploadKnowledgeDocuments: vi.fn(() => ({
    isPending: false,
    mutate: uploadKnowledgeDocuments,
  })),
  useRetryKnowledgeDocument: vi.fn(() => ({
    isPending: false,
    mutate: retryKnowledgeDocument,
  })),
  useReplaceKnowledgeDocument: vi.fn(() => ({
    isPending: false,
    mutate: replaceKnowledgeDocument,
  })),
  useDeleteKnowledgeDocument: vi.fn(() => ({
    isPending: false,
    mutate: deleteKnowledgeDocument,
  })),
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
  beforeEach(() => {
    vi.clearAllMocks()
    vi.mocked(useKnowledgeDocuments).mockReturnValue({ data: [] } as never)
    vi.mocked(useUploadKnowledgeDocuments).mockReturnValue({
      isPending: false,
      mutate: uploadKnowledgeDocuments,
    } as never)
    vi.mocked(useRetryKnowledgeDocument).mockReturnValue({
      isPending: false,
      mutate: retryKnowledgeDocument,
    } as never)
    vi.mocked(useReplaceKnowledgeDocument).mockReturnValue({
      isPending: false,
      mutate: replaceKnowledgeDocument,
    } as never)
    vi.mocked(useDeleteKnowledgeDocument).mockReturnValue({
      isPending: false,
      mutate: deleteKnowledgeDocument,
    } as never)
  })

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
    expect(screen.queryByRole('button', { name: /重试解析/ })).not.toBeInTheDocument()
    expect(screen.queryByLabelText(/选择替换文档/)).not.toBeInTheDocument()
    expect(screen.queryByRole('button', { name: /删除文档/ })).not.toBeInTheDocument()
    expect(screen.queryByRole('button', { name: '删除知识库' })).not.toBeInTheDocument()
  })

  it('allows managers to batch upload, retry, replace and delete documents', async () => {
    const user = userEvent.setup()
    vi.mocked(useKnowledgeDocuments).mockReturnValue({
      data: [{
        provider_id: 'document-1',
        name: 'policy.txt',
        status: 'FAIL',
        size_bytes: 12,
        chunk_count: 0,
      }],
    } as never)
    renderDetail(true)

    await user.upload(screen.getByLabelText('选择文档'), [
      new File(['a'], 'a.txt', { type: 'text/plain' }),
      new File(['b'], 'b.txt', { type: 'text/plain' }),
    ])
    await user.click(screen.getByRole('button', { name: '批量上传并解析' }))
    expect(uploadKnowledgeDocuments).toHaveBeenCalledWith(
      expect.arrayContaining([
        expect.objectContaining({ name: 'a.txt' }),
        expect.objectContaining({ name: 'b.txt' }),
      ]),
      expect.anything(),
    )

    await user.click(screen.getByRole('button', { name: '重试解析 policy.txt' }))
    expect(retryKnowledgeDocument).toHaveBeenCalledWith('document-1')

    const replacement = new File(['new'], 'policy-v2.txt', { type: 'text/plain' })
    await user.upload(screen.getByLabelText('选择替换文档 policy.txt'), replacement)
    expect(replaceKnowledgeDocument).toHaveBeenCalledWith({
      documentId: 'document-1',
      file: expect.objectContaining({ name: 'policy-v2.txt' }),
    })

    await user.click(screen.getByRole('button', { name: '删除文档 policy.txt' }))
    expect(deleteKnowledgeDocument).toHaveBeenCalledWith('document-1')
  })

  it('scopes retry and delete pending state to the acting row only', () => {
    vi.mocked(useKnowledgeDocuments).mockReturnValue({
      data: [
        {
          provider_id: 'document-1',
          name: 'policy.txt',
          status: 'FAIL',
          size_bytes: 12,
          chunk_count: 0,
        },
        {
          provider_id: 'document-2',
          name: 'handbook.txt',
          status: 'FAIL',
          size_bytes: 20,
          chunk_count: 0,
        },
      ],
    } as never)
    vi.mocked(useRetryKnowledgeDocument).mockReturnValue({
      isPending: true,
      variables: 'document-1',
      mutate: retryKnowledgeDocument,
    } as never)
    vi.mocked(useDeleteKnowledgeDocument).mockReturnValue({
      isPending: true,
      variables: 'document-2',
      mutate: deleteKnowledgeDocument,
    } as never)
    renderDetail(true)

    expect(
      screen.getByRole('button', { name: /重试解析 policy\.txt/ }),
    ).toHaveClass('ant-btn-loading')
    expect(
      screen.getByRole('button', { name: '重试解析 handbook.txt' }),
    ).not.toHaveClass('ant-btn-loading')
    expect(
      screen.getByRole('button', { name: /删除文档 handbook\.txt/ }),
    ).toHaveClass('ant-btn-loading')
    expect(
      screen.getByRole('button', { name: '删除文档 policy.txt' }),
    ).not.toHaveClass('ant-btn-loading')
  })

  it('surfaces a visible error when a document operation fails', () => {
    vi.mocked(useKnowledgeDocuments).mockReturnValue({
      data: [
        {
          provider_id: 'document-1',
          name: 'policy.txt',
          status: 'FAIL',
          size_bytes: 12,
          chunk_count: 0,
        },
      ],
    } as never)
    vi.mocked(useRetryKnowledgeDocument).mockReturnValue({
      isPending: false,
      error: new Error('provider unavailable'),
      mutate: retryKnowledgeDocument,
    } as never)
    vi.mocked(useDeleteKnowledgeDocument).mockReturnValue({
      isPending: false,
      error: new Error('provider unavailable'),
      mutate: deleteKnowledgeDocument,
    } as never)
    vi.mocked(useUploadKnowledgeDocuments).mockReturnValue({
      isPending: false,
      error: new Error('provider unavailable'),
      mutate: uploadKnowledgeDocuments,
    } as never)
    vi.mocked(useReplaceKnowledgeDocument).mockReturnValue({
      isPending: false,
      error: new Error('provider unavailable'),
      mutate: replaceKnowledgeDocument,
    } as never)
    renderDetail(true)

    expect(screen.getByText('文档重试解析失败，请稍后重试')).toBeInTheDocument()
    expect(screen.getByText('文档删除失败，请稍后重试')).toBeInTheDocument()
    expect(screen.getByText('文档上传失败，请稍后重试')).toBeInTheDocument()
    expect(screen.getByText('文档替换失败，请稍后重试')).toBeInTheDocument()
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
