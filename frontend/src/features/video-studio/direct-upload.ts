import COS from 'cos-js-sdk-v5'
import { createSHA256 } from 'hash-wasm'

import type { MaterialUploadCredentialResponse } from './api/media-library'

const HASH_CHUNK_SIZE = 4 * 1024 * 1024

export async function sha256File(file: File): Promise<string> {
  const hasher = await createSHA256()
  hasher.init()
  for (let offset = 0; offset < file.size; offset += HASH_CHUNK_SIZE) {
    const chunk = file.slice(offset, Math.min(offset + HASH_CHUNK_SIZE, file.size))
    hasher.update(new Uint8Array(await chunk.arrayBuffer()))
  }
  return hasher.digest('hex')
}

export async function uploadMaterialFile(
  file: File,
  draft: MaterialUploadCredentialResponse,
  onProgress: (percent: number) => void,
): Promise<void> {
  const expiresAt = Math.floor(new Date(draft.credentials.expires_at).getTime() / 1000)
  if (!Number.isFinite(expiresAt) || expiresAt <= Math.floor(Date.now() / 1000)) {
    throw new Error('material upload credentials have expired')
  }
  const client = new COS({
    SecretId: draft.credentials.tmp_secret_id,
    SecretKey: draft.credentials.tmp_secret_key,
    SecurityToken: draft.credentials.session_token,
    StartTime: Math.floor(Date.now() / 1000) - 30,
    ExpiredTime: expiresAt,
    ChunkRetryTimes: 2,
    ChunkParallelLimit: 3,
  })
  await client.uploadFile({
    Bucket: draft.credentials.bucket,
    Region: draft.credentials.region,
    Key: draft.material.storage_key,
    Body: file,
    // 服务端核验数据来源：COS ETag 非内容 sha256，complete-upload 通过
    // head_object 读取该自定义元数据与草稿声明比对。
    Headers: { 'x-cos-meta-sha256': draft.material.sha256 },
    onProgress: ({ percent }) => onProgress(Math.round(percent * 100)),
  })
  onProgress(100)
}
