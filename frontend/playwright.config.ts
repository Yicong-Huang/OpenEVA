import { defineConfig } from '@playwright/test'
export default defineConfig({
  testDir: './e2e',
  workers: 1,
  use: {
    baseURL: 'http://localhost:8021',
    headless: true,
    viewport: { width: 1920, height: 1080 },
  },
  timeout: 30000,
})
