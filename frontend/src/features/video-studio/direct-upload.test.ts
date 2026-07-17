import { describe, expect, it, vi } from 'vitest'

const uploadFileMock = vi.hoisted(() =>
  vi.fn(async (_options: Record<string, unknown>) => ({ statusCode: 200 })),
)

vi.mock('cos-js-sdk-v5', () => ({
  default: class {
    uploadFile = uploadFileMock
  },
}))

import type { MaterialUploadCredentialResponse } from './api/media-library'
import { crc64ecmaFile, sha256File, uploadMaterialFile } from './direct-upload'

describe('B04 COS 直传', () => {
  it('分块计算文件 SHA256', async () => {
    const file = new File(['abc'], 'sample.txt', { type: 'text/plain' })

    await expect(sha256File(file)).resolves.toBe(
      'ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad',
    )
  })

  it('分块计算文件 CRC-64/XZ（十进制，与 COS x-cos-hash-crc64ecma 一致）', async () => {
    // 已知向量 CRC-64/XZ("123456789") = 0x995DC9BBDF1939FA = 11051210869376104954。
    const file = new File(['123456789'], 'vector.bin', { type: 'application/octet-stream' })

    await expect(crc64ecmaFile(file)).resolves.toBe('11051210869376104954')
  })

  it('在请求 COS 前拒绝已过期的临时凭证', async () => {
    vi.useFakeTimers()
    vi.setSystemTime(new Date('2026-07-16T10:00:00Z'))
    const draft = {
      material: {
        id: '00000000-0000-4000-8000-000000000201',
        folder_id: null,
        name: 'expired.mp4',
        kind: 'video',
        media_type: 'video/mp4',
        size_bytes: 3,
        sha256: 'a'.repeat(64),
        crc64ecma: '12345',
        storage_key: 'materials/tenant/material/expired.mp4',
        status: 'pending_upload',
        tags: [],
        cleanup_required: false,
        artifact_id: null,
        created_at: '2026-07-16T09:00:00Z',
        updated_at: '2026-07-16T09:00:00Z',
      },
      credentials: {
        provider: 'tencent-cos',
        bucket: 'bucket-123',
        region: 'ap-beijing',
        key_prefix: 'materials/tenant/material/',
        tmp_secret_id: 'tmp-id',
        tmp_secret_key: 'tmp-key',
        session_token: 'tmp-token',
        expires_at: '2026-07-16T09:59:59Z',
      },
    } satisfies MaterialUploadCredentialResponse

    await expect(
      uploadMaterialFile(
        new File(['abc'], 'expired.mp4', { type: 'video/mp4' }),
        draft,
        vi.fn(),
      ),
    ).rejects.toThrow('material upload credentials have expired')
    vi.useRealTimers()
  })
})


describe('B04 COS 直传元数据', () => {
  it('直传时写入 x-cos-meta-sha256 元数据供服务端核验', async () => {
    uploadFileMock.mockClear()
    const sha = 'b'.repeat(64)
    const draft = {
      material: {
        id: '00000000-0000-4000-8000-000000000202',
        folder_id: null,
        name: 'meta.mp4',
        kind: 'video',
        media_type: 'video/mp4',
        size_bytes: 3,
        sha256: sha,
        crc64ecma: '12345',
        storage_key: 'materials/tenant/material/meta.mp4',
        status: 'pending_upload',
        tags: [],
        cleanup_required: false,
        artifact_id: null,
        created_at: '2026-07-17T09:00:00Z',
        updated_at: '2026-07-17T09:00:00Z',
      },
      credentials: {
        provider: 'tencent-cos',
        bucket: 'bucket-123',
        region: 'ap-beijing',
        key_prefix: 'materials/tenant/material/',
        tmp_secret_id: 'tmp-id',
        tmp_secret_key: 'tmp-key',
        session_token: 'tmp-token',
        expires_at: new Date(Date.now() + 10 * 60 * 1000).toISOString(),
      },
    } satisfies MaterialUploadCredentialResponse

    await uploadMaterialFile(
      new File(['abc'], 'meta.mp4', { type: 'video/mp4' }),
      draft,
      vi.fn(),
    )

    expect(uploadFileMock).toHaveBeenCalledTimes(1)
    const options = uploadFileMock.mock.calls[0][0] as Record<string, unknown>
    expect(options.Headers).toMatchObject({ 'x-cos-meta-sha256': sha })
  })
})
