import { defineConfig } from '@playwright/test';

export default defineConfig({
  testDir: '.',
  timeout: 300_000, // 5 min — agent research takes time
  retries: 0,
  use: {
    baseURL: 'http://localhost:3686',
    headless: true,
    viewport: { width: 1280, height: 900 },
    screenshot: 'on',
    trace: 'on-first-retry',
  },
  reporter: [['list']],
});
