import { defineConfig, devices } from '@playwright/test';

export default defineConfig({
  testDir: './e2e',

  /** Global per-test timeout (ms). Increase for slow CI runners. */
  timeout: 30_000,

  /** Run all tests in all files in parallel — each worker gets its own browser. */
  fullyParallel: true,

  /**
   * Fail immediately if any test has `.only` committed — prevents accidentally
   * shipping a half-suite to CI.
   */
  forbidOnly: !!process.env.CI,

  /**
   * Retry flaky tests automatically:
   * - 2 retries in CI where timing is less predictable
   * - 0 locally so failures surface immediately
   */
  retries: process.env.CI ? 2 : 0,

  /**
   * Worker parallelism:
   * - CI: 1 worker keeps resource usage predictable on shared runners
   * - Local: undefined lets Playwright pick a sensible default (CPU count / 2)
   */
  workers: process.env.CI ? 1 : undefined,

  /** Reporters: HTML report for post-run inspection + compact line output in terminal. */
  reporter: [
    ['html', { open: 'never', outputFolder: 'playwright-report' }],
    ['line'],
  ],

  use: {
    baseURL: 'http://localhost:5173',

    headless: true,

    /** Capture trace on the first retry so failures are debuggable. */
    trace: 'on-first-retry',

    /** Screenshot only on failure — reduces CI artifact size. */
    screenshot: 'only-on-failure',

    /** Retain video only on failure. */
    video: 'retain-on-failure',
  },

  projects: [
    {
      name: 'chromium',
      use: { ...devices['Desktop Chrome'] },
    },
  ],

  webServer: {
    command: 'npm run dev',
    url: 'http://localhost:5173',
    /** Re-use a running dev server locally; always start a fresh one in CI. */
    reuseExistingServer: !process.env.CI,
    timeout: 120_000,
  },
});
