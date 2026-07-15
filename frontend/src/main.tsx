import { StrictMode } from 'react'
import { QueryClientProvider } from '@tanstack/react-query'
import { createRoot } from 'react-dom/client'
import { BrowserRouter } from 'react-router-dom'

import { queryClient } from './api/query-client'
import { configureApiBaseUrl } from './api/client'
import { App } from './app/App'
import { showBootstrapError } from './app/bootstrap-error'
import { getPlatformAdapter } from './platform'
import './index.css'

async function bootstrap() {
  if (import.meta.env.MODE === 'tauri-test') {
    await import('@wdio/tauri-plugin')
  }

  const platform = getPlatformAdapter()
  const runtimeConfig = await platform.runtimeConfig()
  if (runtimeConfig.webUrl !== null && window.location.origin !== new URL(runtimeConfig.webUrl).origin) {
    window.location.replace(runtimeConfig.webUrl)
    return
  }
  if (platform.capabilities().platform === 'tauri' && runtimeConfig.apiBaseUrl === null) {
    throw new Error('desktop_runtime_api_url_missing')
  }
  configureApiBaseUrl(runtimeConfig.apiBaseUrl)

  createRoot(document.getElementById('root')!, {
    onUncaughtError: (error) => window.setTimeout(() => showBootstrapError(error)),
  }).render(
    <StrictMode>
      <QueryClientProvider client={queryClient}>
        <BrowserRouter>
          <App />
        </BrowserRouter>
      </QueryClientProvider>
    </StrictMode>,
  )
}

void bootstrap().catch(showBootstrapError)
