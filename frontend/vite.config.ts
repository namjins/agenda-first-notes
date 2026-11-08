/// <reference types="vitest/config" />
import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'
import tailwindcss from '@tailwindcss/vite'

// Dev: Vite serves the UI on :5173 and proxies /api to the Python backend on :8765.
// Prod: the backend serves ./dist directly, so no dev server is needed.
export default defineConfig({
  plugins: [react(), tailwindcss()],
  server: {
    host: '127.0.0.1',
    port: 5173,
    proxy: { '/api': { target: 'http://127.0.0.1:8765', changeOrigin: false } },
  },
  build: { outDir: 'dist', sourcemap: false },
  test: { environment: 'jsdom', globals: true, setupFiles: ['./src/test-setup.ts'], include: ['src/**/*.test.{ts,tsx}'] },
})
