import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter, Route, Routes } from 'react-router-dom'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import type { PlatformAdapter } from '../../../platform'
import { useLogin } from '../api/queries'
import { LoginPage } from './LoginPage'

const platformMocks = vi.hoisted(() => ({
  get: vi.fn(),
  set: vi.fn(),
  delete: vi.fn(),
}))

vi.mock('../api/queries', () => ({ useLogin: vi.fn() }))
vi.mock('../../../platform', () => ({
  getPlatformAdapter: () => ({
    capabilities: () => ({ rememberedLogin: true }),
    rememberedLogin: platformMocks,
  } as unknown as PlatformAdapter),
}))

const mutateAsync = vi.fn()

function renderLogin() {
  return render(
    <MemoryRouter initialEntries={['/login']}>
      <Routes>
        <Route path="/login" element={<LoginPage />} />
        <Route path="/" element={<div>工作台</div>} />
      </Routes>
    </MemoryRouter>,
  )
}

describe('LoginPage remembered desktop login', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    platformMocks.get.mockResolvedValue(null)
    platformMocks.set.mockResolvedValue(undefined)
    platformMocks.delete.mockResolvedValue(undefined)
    mutateAsync.mockResolvedValue(undefined)
    vi.mocked(useLogin).mockReturnValue({
      mutateAsync,
      isPending: false,
      isError: false,
      error: null,
    } as never)
  })

  it('restores the saved email and password from App-private storage', async () => {
    platformMocks.get.mockResolvedValue(JSON.stringify({
      email: 'demo@example.com',
      password: 'agent-platform-demo',
    }))
    renderLogin()

    await waitFor(() => {
      expect(screen.getByLabelText('邮箱')).toHaveValue('demo@example.com')
      expect(screen.getByLabelText('密码')).toHaveValue('agent-platform-demo')
    })
    expect(screen.getByRole('checkbox', { name: /记住账号和密码/ })).toBeChecked()
  })

  it('saves the login only after authentication succeeds', async () => {
    const user = userEvent.setup()
    renderLogin()

    await user.type(screen.getByLabelText('邮箱'), 'demo@example.com')
    await user.type(screen.getByLabelText('密码'), 'agent-platform-demo')
    await user.click(screen.getByRole('button', { name: /登\s*录/ }))

    await waitFor(() => expect(mutateAsync).toHaveBeenCalledWith({
      email: 'demo@example.com',
      password: 'agent-platform-demo',
    }))
    expect(platformMocks.set).toHaveBeenCalledWith(JSON.stringify({
      email: 'demo@example.com',
      password: 'agent-platform-demo',
    }))
    expect(await screen.findByText('工作台')).toBeInTheDocument()
  })

  it('does not overwrite the saved login when authentication fails', async () => {
    const user = userEvent.setup()
    mutateAsync.mockRejectedValueOnce(new Error('invalid credentials'))
    renderLogin()

    await user.type(screen.getByLabelText('邮箱'), 'wrong@example.com')
    await user.type(screen.getByLabelText('密码'), 'wrong-password')
    await user.click(screen.getByRole('button', { name: /登\s*录/ }))

    await waitFor(() => expect(mutateAsync).toHaveBeenCalled())
    expect(platformMocks.set).not.toHaveBeenCalled()
    expect(platformMocks.delete).not.toHaveBeenCalled()
  })

  it('clears the saved login after a successful login when remember is unchecked', async () => {
    const user = userEvent.setup()
    renderLogin()

    await user.type(screen.getByLabelText('邮箱'), 'demo@example.com')
    await user.type(screen.getByLabelText('密码'), 'agent-platform-demo')
    await user.click(screen.getByRole('checkbox', { name: /记住账号和密码/ }))
    await user.click(screen.getByRole('button', { name: /登\s*录/ }))

    await waitFor(() => expect(platformMocks.delete).toHaveBeenCalledOnce())
    expect(platformMocks.set).not.toHaveBeenCalled()
    expect(await screen.findByText('工作台')).toBeInTheDocument()
  })
})
