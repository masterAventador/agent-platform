import { defineConfig } from 'vitest/config'
import react from '@vitejs/plugin-react'

// https://vite.dev/config/
export default defineConfig({
  plugins: [react()],
  build: {
    rolldownOptions: {
      output: {
        codeSplitting: {
          groups: [
            {
              name: 'vendor-react',
              test: /node_modules\/(?:react|react-dom|react-router|react-router-dom|scheduler)\//,
            },
            {
              name: 'vendor-antd',
              test: /node_modules\/(?:antd|@ant-design)\//,
              maxSize: 400_000,
            },
            {
              name: 'vendor-rc',
              test: /node_modules\/(?:@rc-component|rc-[^/]+)\//,
            },
            {
              name: 'vendor-data',
              test: /node_modules\/(?:@tanstack|axios|zustand|zod)\//,
            },
          ],
        },
      },
    },
  },
  server: {
    proxy: {
      '/api': 'http://127.0.0.1:8000',
    },
  },
  test: {
    environment: 'jsdom',
    include: ['src/**/*.test.{ts,tsx}'],
    setupFiles: ['./src/test/setup.ts'],
  },
})
