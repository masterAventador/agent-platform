import COS from 'cos-js-sdk-v5'
import { createCRC64, createSHA256 } from 'hash-wasm'

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

// COS 服务端计算的 x-cos-hash-crc64ecma 采用 CRC-64/XZ（ECMA-182 反射多项式，
// hash-wasm createCRC64 默认即此变体）。声明值必须与 COS 服务端值逐位一致，
// 服务端 complete-upload 以此为可信内容指纹核验，故这里输出十进制 uint64 字符串。
export async function crc64ecmaFile(file: File): Promise<string> {
  const hasher = await createCRC64()
  hasher.init()
  for (let offset = 0; offset < file.size; offset += HASH_CHUNK_SIZE) {
    const chunk = file.slice(offset, Math.min(offset + HASH_CHUNK_SIZE, file.size))
    hasher.update(new Uint8Array(await chunk.arrayBuffer()))
  }
  return BigInt(`0x${hasher.digest('hex')}`).toString()
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
    // 服务端可信核验用 COS 计算的 x-cos-hash-crc64ecma（无需客户端提供）。这里携带
    // sha256 仅作展示与意外损坏诊断的自定义元数据，非安全门禁（可伪造）。
    Headers: { 'x-cos-meta-sha256': draft.material.sha256 },
    onProgress: ({ percent }) => onProgress(Math.round(percent * 100)),
  })
  onProgress(100)
}
