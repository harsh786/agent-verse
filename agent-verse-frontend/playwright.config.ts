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
  reporter: [['html', { outputFolder: 'playwright-report' }], ['line']],

  use: {
    baseURL: process.env.BASE_URL ?? 'http://localhost:5173',
    trace: 'on-first-retry',
    screenshot: 'only-on-failure',
    video: 'retain-on-failure',
  },

  projects: [
    // Smoke test - fast, critical paths only
    {
      name: 'smoke-live',
      testMatch: ['**/smoke/**', '**/*.smoke.spec.ts'],
      use: { ...devices['Desktop Chrome'] },
    },
    // Full test suite
    {
      name: 'full-live',
      testMatch: ['**/*.spec.ts'],
      use: { ...devices['Desktop Chrome'] },
    },
    // Mobile viewport
    {
      name: 'mobile',
      testMatch: ['**/*.spec.ts'],
      use: { ...devices['Pixel 5'] },
    },
    // Accessibility
    {
      name: 'accessibility',
      testMatch: ['**/accessibility/**', '**/*.a11y.spec.ts'],
      use: { ...devices['Desktop Chrome'] },
    },
    // Security smoke
    {
      name: 'security-smoke',
      testMatch: ['**/security/**', '**/*.security.spec.ts'],
      use: { ...devices['Desktop Chrome'] },
    },
    // Failure states
    {
      name: 'failure-states',
      testMatch: ['**/failure-states/**', '**/*.failure.spec.ts'],
      use: { ...devices['Desktop Chrome'] },
    },
    // Provider live tests (gated behind env var)
    {
      name: 'provider-live',
      testMatch: ['**/provider-live/**', '**/*.provider.spec.ts'],
      use: { ...devices['Desktop Chrome'] },
    },
    // Eval regression
    {
      name: 'eval-regression',
      testMatch: ['**/eval-regression/**', '**/*.eval.spec.ts'],
      use: { ...devices['Desktop Chrome'] },
    },
    // Multimodal live tests
    {
      name: 'multimodal-live',
      testMatch: ['**/multimodal/**', '**/*.multimodal.spec.ts'],
      use: { ...devices['Desktop Chrome'] },
    },
    // RAG live tests
    {
      name: 'rag-live',
      testMatch: ['**/rag-live/**', '**/*.rag.spec.ts'],
      use: { ...devices['Desktop Chrome'] },
    },
    // Governance live tests
    {
      name: 'governance-live',
      testMatch: ['**/governance-live/**', '**/*.governance.spec.ts'],
      use: { ...devices['Desktop Chrome'] },
    },
    // Observability live tests
    {
      name: 'observability-live',
      testMatch: ['**/observability-live/**', '**/*.observability.spec.ts'],
      use: { ...devices['Desktop Chrome'] },
    },
  ],

  webServer: {
    command: 'npm run dev',
    url: 'http://localhost:5173',
    reuseExistingServer: !process.env.CI,
    timeout: 120_000,
  },
});
