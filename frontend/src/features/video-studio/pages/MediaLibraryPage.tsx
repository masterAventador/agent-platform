import { Alert, Button, Card, Form, Input, Space, Table, Tag, Typography } from 'antd'
import { useEffect, useState } from 'react'

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
  type DownloadTask,
  type MaterialKind,
  type MaterialFolder,
  type MaterialUploadCredentialResponse,
  type MaterialPreview,
  type VideoMaterial,
} from '../api/media-library'
import { sha256File, uploadMaterialFile } from '../direct-upload'

const kindLabels: Record<MaterialKind, string> = {
  video: '视频',
  image: '图片',
  music: '音乐',
}

interface MediaLibraryPageProps {
  workspaceId: string
}

type Notice = { type: 'success' | 'error' | 'warning'; message: string }

export function MediaLibraryPage({ workspaceId }: MediaLibraryPageProps) {
  const [folders, setFolders] = useState<MaterialFolder[]>([])
  const [materials, setMaterials] = useState<VideoMaterial[]>([])
  const [downloadTasks, setDownloadTasks] = useState<DownloadTask[]>([])
  const [draft, setDraft] = useState<MaterialUploadCredentialResponse>()
  const [preview, setPreview] = useState<MaterialPreview>()
  const [notice, setNotice] = useState<Notice>()
  const [busy, setBusy] = useState(false)
  const [selectedFile, setSelectedFile] = useState<File>()
  const [uploadProgress, setUploadProgress] = useState(0)
  const [folderName, setFolderName] = useState('')
  const [selectedFolderId, setSelectedFolderId] = useState('')
  const [kind, setKind] = useState<MaterialKind>('video')
  const [tags, setTags] = useState('')

  useEffect(() => {
    let active = true
    void Promise.all([
      listMaterialFolders(workspaceId),
      listMaterials(workspaceId),
      listDownloadTasks(workspaceId),
    ])
      .then(([folderItems, materialItems, taskItems]) => {
        if (!active) return
        setFolders(folderItems)
        setMaterials(materialItems)
        setDownloadTasks(taskItems)
      })
      .catch(() => {
        if (active) setNotice({ type: 'warning', message: '素材库暂不可用，请稍后重试。' })
      })
    return () => { active = false }
  }, [workspaceId])

  const perform = async (operation: () => Promise<void>, successMessage: string) => {
    if (busy) return
    setBusy(true)
    setNotice(undefined)
    try {
      await operation()
      setNotice({ type: 'success', message: successMessage })
    } catch {
      setNotice({ type: 'error', message: '素材库操作失败，请检查权限、凭证有效期和网络状态。' })
    } finally {
      setBusy(false)
    }
  }

  const createFolder = () => perform(async () => {
    const folder = await createMaterialFolder(workspaceId, {
      name: folderName.trim(),
      parent_id: null,
    })
    setFolders((current) => [
      ...current.filter((item) => item.id !== folder.id),
      folder,
    ])
    setSelectedFolderId(folder.id)
    setFolderName('')
  }, '素材文件夹已创建。')

  const uploadSelectedFile = () => perform(async () => {
    if (selectedFile === undefined) throw new Error('missing material file')
    setDraft(undefined)
    setUploadProgress(0)
    const sha256 = await sha256File(selectedFile)
    const response = await requestMaterialUploadCredentials(workspaceId, {
      name: selectedFile.name,
      kind,
      media_type: selectedFile.type || defaultMediaType(kind),
      size_bytes: selectedFile.size,
      sha256,
      folder_id: selectedFolderId || null,
      tag_names: tags.split(',').map((tag) => tag.trim()).filter(Boolean),
    })
    try {
      await uploadMaterialFile(selectedFile, response, setUploadProgress)
    } catch (error) {
      await abortMaterialUpload(workspaceId, response.material.id).catch(() => undefined)
      throw error
    }
    setDraft(response)
  }, '素材已直传，等待服务端核验。')

  const completeUpload = () => perform(async () => {
    if (draft === undefined) throw new Error('missing upload draft')
    const completed = await completeMaterialUpload(
      workspaceId,
      draft.material.id,
    )
    setMaterials((current) => [
      ...current.filter((item) => item.id !== completed.id),
      completed,
    ])
    setDraft(undefined)
    setSelectedFile(undefined)
    setUploadProgress(0)
  }, '素材已确认上传完成。')

  const createDownload = (materialId: string) => perform(async () => {
    const task = await createDownloadTask(workspaceId, {
      source_type: 'material',
      source_id: materialId,
    })
    setDownloadTasks((current) => [
      ...current.filter((item) => item.id !== task.id),
      task,
    ])
  }, '下载任务已创建。')

  const previewMaterial = (materialId: string) => perform(async () => {
    setPreview(await requestMaterialPreview(workspaceId, materialId))
  }, '素材预览链接已生成。')

  const removeMaterial = (materialId: string) => perform(async () => {
    await deleteMaterial(workspaceId, materialId)
    setMaterials((current) => current.filter((item) => item.id !== materialId))
    setPreview(undefined)
  }, '素材已删除，存储清理任务将异步执行。')

  const retryDownload = (taskId: string) => perform(async () => {
    const task = await retryDownloadTask(workspaceId, taskId)
    setDownloadTasks((current) => [
      ...current.filter((item) => item.id !== task.id),
      task,
    ])
  }, '下载任务已重新排队。')

  const firstMaterial = materials[0]
  const firstRetryableTask = downloadTasks.find((task) => task.status === 'failed' && task.retryable)

  return (
    <Space orientation="vertical" size="large" style={{ width: '100%' }}>
      <Typography.Title level={2}>素材库</Typography.Title>
      {notice !== undefined && <Alert type={notice.type} title={notice.message} showIcon />}

      <Card title="素材文件夹">
        <Space orientation="vertical">
          <Form layout="inline">
            <Form.Item label="文件夹名称">
              <Input
                aria-label="文件夹名称"
                value={folderName}
                onChange={(event) => setFolderName(event.target.value)}
              />
            </Form.Item>
            <Button disabled={busy} onClick={createFolder}>创建文件夹</Button>
          </Form>
          <Space wrap>
            {folders.map((folder) => (
              <Tag key={folder.id}>{folder.name}</Tag>
            ))}
          </Space>
        </Space>
      </Card>

      <Card title="直传上传">
        <Form layout="vertical">
          <Form.Item label="素材文件">
            <input
              aria-label="素材文件"
              type="file"
              accept="video/*,image/*,audio/*"
              onChange={(event) => {
                setSelectedFile(event.target.files?.[0])
                setDraft(undefined)
                setUploadProgress(0)
              }}
            />
          </Form.Item>
          <Form.Item label="素材文件夹">
            <select
              aria-label="素材文件夹"
              value={selectedFolderId}
              onChange={(event) => setSelectedFolderId(event.target.value)}
            >
              <option value="">不放入文件夹</option>
              {folders.map((folder) => (
                <option key={folder.id} value={folder.id}>{folder.name}</option>
              ))}
            </select>
          </Form.Item>
          <Form.Item label="素材类型">
            <select aria-label="素材类型" value={kind} onChange={(event) => setKind(event.target.value as MaterialKind)}>
              <option value="video">视频</option>
              <option value="image">图片</option>
              <option value="music">音乐</option>
            </select>
          </Form.Item>
          <Form.Item label="标签">
            <Input aria-label="标签" value={tags} onChange={(event) => setTags(event.target.value)} />
          </Form.Item>
          <Space>
            <Button
              type="primary"
              disabled={busy || selectedFile === undefined}
              onClick={uploadSelectedFile}
            >
              上传到素材库
            </Button>
            <Button disabled={busy || draft === undefined} onClick={completeUpload}>
              确认上传完成
            </Button>
          </Space>
          {uploadProgress > 0 && <Typography.Text>上传进度：{uploadProgress}%</Typography.Text>}
        </Form>
        {draft !== undefined && (
          <Alert
            type="info"
            showIcon
            title="临时上传凭证"
            description={(
              <Space orientation="vertical">
                <span>{draft.credentials.key_prefix}</span>
                <span>过期时间：{draft.credentials.expires_at}</span>
              </Space>
            )}
          />
        )}
      </Card>

      <Card title="素材列表">
        <Table
          rowKey="id"
          dataSource={materials}
          pagination={false}
          columns={[
            { title: '名称', dataIndex: 'name' },
            { title: '类型', render: (_, item) => kindLabels[item.kind] },
            { title: '状态', dataIndex: 'status' },
            {
              title: '标签',
              render: (_, item) => item.tags.map((tag) => <Tag key={tag}>{tag}</Tag>),
            },
            {
              title: '操作',
              render: (_, item) => (
                <Space>
                  <Button disabled={busy} onClick={() => void previewMaterial(item.id)}>
                    预览 {item.name}
                  </Button>
                  <Button danger disabled={busy} onClick={() => void removeMaterial(item.id)}>
                    删除 {item.name}
                  </Button>
                </Space>
              ),
            },
          ]}
        />
        {preview !== undefined && (
          <a href={preview.url} target="_blank" rel="noreferrer">
            打开短时预览
          </a>
        )}
        <Button
          disabled={firstMaterial === undefined}
          aria-busy={busy}
          onClick={() => { if (firstMaterial !== undefined) void createDownload(firstMaterial.id) }}
        >
          创建下载任务
        </Button>
      </Card>

      <Card title="下载任务">
        <Table
          rowKey="id"
          dataSource={downloadTasks}
          pagination={false}
          columns={[
            { title: '状态', dataIndex: 'status' },
            { title: '进度', dataIndex: 'progress' },
            { title: '错误', dataIndex: 'error_code' },
            { title: '断点', dataIndex: 'resume_token' },
          ]}
        />
        <Button
          disabled={firstRetryableTask === undefined}
          aria-busy={busy}
          onClick={() => { if (firstRetryableTask !== undefined) void retryDownload(firstRetryableTask.id) }}
        >
          重试下载
        </Button>
      </Card>
    </Space>
  )
}

function defaultMediaType(kind: MaterialKind): string {
  if (kind === 'image') return 'image/png'
  if (kind === 'music') return 'audio/mpeg'
  return 'video/mp4'
}
