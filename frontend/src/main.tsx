import { StrictMode } from 'react'
import { QueryClientProvider } from '@tanstack/react-query'
import { createRoot } from 'react-dom/client'
import { BrowserRouter } from 'react-router-dom'

import { queryClient } from './api/query-client'
import { App } from './app/App'
import { getPlatformAdapter } from './platform'
import './index.css'

async function bootstrap() {
  if (import.meta.env.MODE === 'tauri-test') {
    await import('@wdio/tauri-plugin')
  }

  getPlatformAdapter()

  createRoot(document.getElementById('root')!).render(
    <StrictMode>
      <QueryClientProvider client={queryClient}>
        <BrowserRouter>
          <App />
        </BrowserRouter>
      </QueryClientProvider>
    </StrictMode>,
  )
}

void bootstrap()
