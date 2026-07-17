import { beforeEach, describe, expect, it, vi } from 'vitest'

import { apiClient } from '../../../api/client'
import { listMaterials } from './media-library'

vi.mock('../../../api/client', () => ({
  apiClient: { get: vi.fn() },
}))

const tenantId = '10000000-0000-4000-8000-000000000010'
const materialId = '20000000-0000-4000-8000-000000000020'

function materialPayload(overrides: Record<string, unknown> = {}) {
  return {
    id: materialId,
    folder_id: null,
    name: 'legacy.mp4',
    kind: 'video',
    media_type: 'video/mp4',
    size_bytes: 1024,
    sha256: 'a'.repeat(64),
    crc64ecma: '11051210869376104954',
    storage_key: `materials/${tenantId}/${materialId}/legacy.mp4`,
    status: 'available',
    tags: [],
    cleanup_required: false,
    artifact_id: null,
    created_at: '2026-07-17T00:00:00Z',
    updated_at: '2026-07-17T00:00:00Z',
    ...overrides,
  }
}

describe('media-library API 素材解析', () => {
  beforeEach(() => vi.clearAllMocks())

  it('解析带真实 crc64 的可用素材', async () => {
    vi.mocked(apiClient.get).mockResolvedValue({ data: { items: [materialPayload()] } })

    const materials = await listMaterials(tenantId)

    expect(materials).toHaveLength(1)
    expect(materials[0].crc64ecma).toBe('11051210869376104954')
  })

  it('接受迁移 0033 之前完成、crc64 回填为空串的存量可用素材（crc64 仅展示、可缺失）', async () => {
    // 迁移 0033 给存量 available 行回填 crc64ecma=''；后端忠实回显，前端读契约必须容忍，
    // 否则单条空 crc64 会让整个素材库列表解析崩溃、该 workspace 页面不可用。
    vi.mocked(apiClient.get).mockResolvedValue({
      data: { items: [materialPayload({ crc64ecma: '' })] },
    })

    const materials = await listMaterials(tenantId)

    expect(materials).toHaveLength(1)
    expect(materials[0].crc64ecma).toBe('')
  })

  it('仍拒绝非法的非空 crc64（非纯数字/超 20 位）', async () => {
    vi.mocked(apiClient.get).mockResolvedValue({
      data: { items: [materialPayload({ crc64ecma: 'not-a-number' })] },
    })

    await expect(listMaterials(tenantId)).rejects.toThrow()
  })
})
