import { defineConfig, devices } from '@playwright/test';

export default defineConfig({
  testDir: './tests',
  timeout: 180_000,
  expect: { timeout: 10_000 },
  workers: 1,
  reporter: 'list',
  use: {
    baseURL: 'http://127.0.0.1:8766',
    channel: 'msedge',
    trace: 'retain-on-failure',
    screenshot: 'only-on-failure',
  },
  projects: [
    {
      name: 'desktop',
      use: { ...devices['Desktop Chrome'], viewport: { width: 1440, height: 1040 } },
    },
    { name: 'mobile', use: { ...devices['iPhone 13'], defaultBrowserType: 'chromium' } },
  ],
  webServer: {
    command:
      '..\\.venv\\Scripts\\python.exe -m uvicorn server.app:app --app-dir .. --host 127.0.0.1 --port 8766',
    env: { TRANSCRIPT_DATA_DIR: '../.cache/browser-test-data', TRANSCRIPT_PORT: '8766' },
    url: 'http://127.0.0.1:8766/api/health',
    reuseExistingServer: false,
    timeout: 30_000,
  },
});
