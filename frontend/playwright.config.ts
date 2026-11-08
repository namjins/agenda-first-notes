import { defineConfig } from '@playwright/test'
import { mkdtempSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { join } from 'node:path'

// The backend runs with AFN_PROVIDER=fake (rule-based stand-in, synthetic data only) in an isolated data dir.
const dataDir = process.env.AFN_DATA_DIR ?? mkdtempSync(join(tmpdir(), 'afn-e2e-'))
process.env.AFN_DATA_DIR = dataDir
const PORT = 8766

export default defineConfig({
  testDir: './e2e',
  timeout: 60_000,
  retries: 0,
  use: { baseURL: `http://127.0.0.1:${PORT}`, trace: 'retain-on-failure' },
  webServer: {
    command: `uv run --directory ../backend agenda-first-notes serve --no-browser --port ${PORT}`,
    url: `http://127.0.0.1:${PORT}/api/health`,
    reuseExistingServer: false,
    env: { AFN_DATA_DIR: dataDir, AFN_PROVIDER: 'fake' },
    timeout: 60_000,
  },
})
