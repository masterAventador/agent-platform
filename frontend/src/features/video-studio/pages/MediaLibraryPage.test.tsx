import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import {
  abortMaterialUpload,
  completeMaterialUpload,
  createMaterialFolder,
  createDownloadTask,
  deleteMaterial,
  listMaterialFolders,
  listDownloadTasks,
  listMaterials,
  requestMaterialUploadCredentials,
  requestMaterialPreview,
  retryDownloadTask,
  type VideoMaterial,
} from '../api/media-library'
import { crc64ecmaFile, sha256File, uploadMaterialFile } from '../direct-upload'
import { MediaLibraryPage } from './MediaLibraryPage'

vi.mock('../api/media-library', () => ({
  abortMaterialUpload: vi.fn(),
  completeMaterialUpload: vi.fn(),
  createMaterialFolder: vi.fn(),
  createDownloadTask: vi.fn(),
  deleteMaterial: vi.fn(),
  listMaterialFolders: vi.fn(),
  listDownloadTasks: vi.fn(),
  listMaterials: vi.fn(),
  requestMaterialUploadCredentials: vi.fn(),
  requestMaterialPreview: vi.fn(),
  retryDownloadTask: vi.fn(),
}))

vi.mock('../direct-upload', () => ({
  sha256File: vi.fn(),
  crc64ecmaFile: vi.fn(),
  uploadMaterialFile: vi.fn(),
}))

const workspaceId = '00000000-0000-4000-8000-000000000101'
const materialId = '00000000-0000-4000-8000-000000000201'
const folderId = '00000000-0000-4000-8000-000000000211'
const downloadTaskId = '00000000-0000-4000-8000-000000000301'
const createdDownloadTaskId = '00000000-0000-4000-8000-000000000302'

const material: VideoMaterial = {
  id: materialId,
  folder_id: null,
  name: 'campaign.mp4',
  kind: 'video' as const,
  media_type: 'video/mp4',
  size_bytes: 1024,
  sha256: 'a'.repeat(64),
  crc64ecma: '11051210869376104954',
  storage_key: `materials/${workspaceId}/${materialId}/campaign.mp4`,
  status: 'available',
  tags: ['7月', '广告'],
  cleanup_required: false,
  artifact_id: null,
  created_at: '2026-07-16T02:00:00Z',
  updated_at: '2026-07-16T02:00:00Z',
}

describe('B04 素材库页面', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    vi.mocked(listMaterialFolders).mockResolvedValue([
      {
        id: folderId,
        parent_id: null,
        name: '广告素材',
        created_by: '00000000-0000-4000-8000-000000000111',
        created_at: '2026-07-16T02:00:00Z',
      },
    ])
    vi.mocked(listMaterials).mockResolvedValue([material])
    vi.mocked(listDownloadTasks).mockResolvedValue([
      {
        id: downloadTaskId,
        source_type: 'material',
        source_id: materialId,
        status: 'failed',
        progress: 40,
        downloaded_bytes: 400,
        total_bytes: 1000,
        resume_token: 'bytes=400-',
        error_code: 'network_timeout',
        retryable: true,
        retry_count: 0,
        revision: 2,
        created_at: '2026-07-16T02:00:00Z',
        updated_at: '2026-07-16T02:01:00Z',
        completed_at: '2026-07-16T02:01:00Z',
      },
    ])
    vi.mocked(requestMaterialUploadCredentials).mockResolvedValue({
      material: { ...material, status: 'pending_upload' },
      credentials: {
        provider: 'tencent-cos',
        bucket: 'agent-platform-materials',
        region: 'ap-beijing',
        key_prefix: `materials/${workspaceId}/${materialId}/`,
        tmp_secret_id: 'tmp-id',
        tmp_secret_key: 'tmp-key',
        session_token: 'tmp-token',
        expires_at: '2026-07-16T02:15:00Z',
      },
    })
    vi.mocked(completeMaterialUpload).mockResolvedValue(material)
    vi.mocked(abortMaterialUpload).mockResolvedValue({
      ...material,
      status: 'upload_failed',
      cleanup_required: true,
    })
    vi.mocked(sha256File).mockResolvedValue('b'.repeat(64))
    vi.mocked(crc64ecmaFile).mockResolvedValue('11051210869376104954')
    vi.mocked(uploadMaterialFile).mockResolvedValue()
    vi.mocked(requestMaterialPreview).mockResolvedValue({
      url: 'https://preview.invalid/campaign.mp4',
      expires_at: '2026-07-16T02:05:00Z',
    })
    vi.mocked(deleteMaterial).mockResolvedValue()
    vi.mocked(createMaterialFolder).mockResolvedValue({
      id: '00000000-0000-4000-8000-000000000212',
      parent_id: null,
      name: '7月素材',
      created_by: '00000000-0000-4000-8000-000000000111',
      created_at: '2026-07-16T02:02:00Z',
    })
    vi.mocked(createDownloadTask).mockResolvedValue({
      id: createdDownloadTaskId,
      source_type: 'material',
      source_id: materialId,
      status: 'queued',
      progress: 0,
      downloaded_bytes: 0,
      total_bytes: 1024,
      resume_token: null,
      error_code: null,
      retryable: false,
      retry_count: 0,
      revision: 0,
      created_at: '2026-07-16T02:02:00Z',
      updated_at: '2026-07-16T02:02:00Z',
      completed_at: null,
    })
    vi.mocked(retryDownloadTask).mockResolvedValue({
      id: downloadTaskId,
      source_type: 'material',
      source_id: materialId,
      status: 'queued',
      progress: 40,
      downloaded_bytes: 400,
      total_bytes: 1000,
      resume_token: 'bytes=400-',
      error_code: null,
      retryable: false,
      retry_count: 1,
      revision: 3,
      created_at: '2026-07-16T02:00:00Z',
      updated_at: '2026-07-16T02:03:00Z',
      completed_at: null,
    })
  })

  // 该用例覆盖完整上传-下载-删除链路，全量套件并发下接近默认 5s 超时，放宽到 15s。
  it('展示素材、签发短期上传凭证、确认上传并管理下载任务', { timeout: 15_000 }, async () => {
    const user = userEvent.setup()
    render(<MediaLibraryPage workspaceId={workspaceId} />)

    expect(await screen.findByText('campaign.mp4')).toBeInTheDocument()
    expect(screen.getAllByText('视频').length).toBeGreaterThanOrEqual(1)
    expect(screen.getByText('广告')).toBeInTheDocument()
    expect(screen.getAllByText('广告素材').length).toBeGreaterThanOrEqual(1)
    expect(screen.getByText('network_timeout')).toBeInTheDocument()

    await user.type(screen.getByLabelText('文件夹名称'), '7月素材')
    await user.click(screen.getByRole('button', { name: '创建文件夹' }))
    expect(createMaterialFolder).toHaveBeenCalledWith(
      workspaceId,
      { name: '7月素材', parent_id: null },
    )

    const uploadFile = new File(['video bytes'], 'launch.mp4', { type: 'video/mp4' })
    await user.upload(screen.getByLabelText('素材文件'), uploadFile)
    await user.selectOptions(screen.getByLabelText('素材文件夹'), folderId)
    await user.selectOptions(screen.getByLabelText('素材类型'), 'video')
    await user.type(screen.getByLabelText('标签'), '广告,7月')
    await user.click(screen.getByRole('button', { name: '上传到素材库' }))

    expect(requestMaterialUploadCredentials).toHaveBeenCalledWith(
      workspaceId,
      expect.objectContaining({
        name: 'launch.mp4',
        kind: 'video',
        media_type: 'video/mp4',
        size_bytes: uploadFile.size,
        sha256: 'b'.repeat(64),
        crc64ecma: '11051210869376104954',
        folder_id: folderId,
        tag_names: ['广告', '7月'],
      }),
    )
    expect(uploadMaterialFile).toHaveBeenCalledWith(
      uploadFile,
      expect.objectContaining({ material: expect.objectContaining({ id: materialId }) }),
      expect.any(Function),
    )
    expect(await screen.findByText(`materials/${workspaceId}/${materialId}/`)).toBeInTheDocument()

    await user.click(screen.getByRole('button', { name: '确认上传完成' }))
    expect(completeMaterialUpload).toHaveBeenCalledWith(
      workspaceId,
      materialId,
    )

    await user.click(screen.getByRole('button', { name: '预览 campaign.mp4' }))
    expect(requestMaterialPreview).toHaveBeenCalledWith(workspaceId, materialId)
    expect(await screen.findByRole('link', { name: '打开短时预览' })).toHaveAttribute(
      'href',
      'https://preview.invalid/campaign.mp4',
    )

    await user.click(screen.getByRole('button', { name: '创建下载任务' }))
    expect(createDownloadTask).toHaveBeenCalledWith(
      workspaceId,
      { source_type: 'material', source_id: materialId },
    )

    await user.click(screen.getByRole('button', { name: '重试下载' }))
    expect(retryDownloadTask).toHaveBeenCalledWith(workspaceId, downloadTaskId)

    await user.click(screen.getByRole('button', { name: '删除 campaign.mp4' }))
    expect(deleteMaterial).toHaveBeenCalledWith(workspaceId, materialId)
  })

  it('直传失败时终止草稿且不允许确认完成', async () => {
    vi.mocked(uploadMaterialFile).mockRejectedValueOnce(new Error('network failure'))
    const user = userEvent.setup()
    render(<MediaLibraryPage workspaceId={workspaceId} />)
    await screen.findByText('campaign.mp4')

    const uploadFile = new File(['broken bytes'], 'broken.mp4', { type: 'video/mp4' })
    await user.upload(screen.getByLabelText('素材文件'), uploadFile)
    await user.click(screen.getByRole('button', { name: '上传到素材库' }))

    expect(abortMaterialUpload).toHaveBeenCalledWith(workspaceId, materialId)
    expect(screen.getByRole('button', { name: '确认上传完成' })).toBeDisabled()
    expect(await screen.findByText('素材库操作失败，请检查权限、凭证有效期和网络状态。')).toBeInTheDocument()
  })
})
