import { renderHook, waitFor } from '@testing-library/react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import type { PropsWithChildren } from 'react'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import { apiClient } from '../../api/client'
import { useCapabilityRegistry } from './queries'

const loadAuthorizedModules = vi.hoisted(() => vi.fn())

vi.mock('./modules', () => ({
  loadAuthorizedFrontendCapabilityModules: loadAuthorizedModules,
}))

const apiGet = vi.spyOn(apiClient, 'get')

function wrapper({ children }: PropsWithChildren) {
  return (
    <QueryClientProvider client={new QueryClient({
      defaultOptions: { queries: { retry: false } },
    })}
    >
      {children}
    </QueryClientProvider>
  )
}

describe('capability registry queries', () => {
  beforeEach(() => {
    apiGet.mockReset()
    loadAuthorizedModules.mockReset()
  })

  it('registry 请求失败时不调用模块 loader', async () => {
    apiGet.mockRejectedValueOnce(new Error('registry unavailable'))
    const { result } = renderHook(
      () => useCapabilityRegistry('workspace-1', ['social.read']),
      { wrapper },
    )

    await waitFor(() => expect(result.current.registry.isError).toBe(true))
    expect(loadAuthorizedModules).not.toHaveBeenCalled()
  })

  it('registry 响应畸形时不调用模块 loader', async () => {
    apiGet.mockResolvedValueOnce({
      data: {
        schema_version: '1.0',
        capabilities: [{
          capability_id: 'social-operations',
          deployment_installed: true,
          tenant_entitled: true,
          frontend_entries: ['social.routes.v1'],
          permissions: [],
        }],
      },
    })
    const { result } = renderHook(
      () => useCapabilityRegistry('workspace-1', ['social.read']),
      { wrapper },
    )

    await waitFor(() => expect(result.current.registry.isError).toBe(true))
    expect(loadAuthorizedModules).not.toHaveBeenCalled()
  })
})
