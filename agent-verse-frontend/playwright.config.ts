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
    baseURL: process.env.BASE_URL ?? 'http://localhost:5174',
    trace: 'on-first-retry',
    screenshot: 'only-on-failure',
    video: 'retain-on-failure',
  },

  projects: [
    {
      name: 'program-13',
      testMatch: ['**/coordination-accessibility.spec.ts', '**/coordination-responsive.spec.ts'],
      use: { ...devices['Desktop Chrome'] },
    },
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
    // REAL-BACKEND suite: drives the app against a genuinely running backend
    // (a real :8000 API + Postgres + Redis), NOT mocked. Selected explicitly via
    // `--project=real-backend`; the CI job (nightly `playwright-real-backend`)
    // starts the backend + services + frontend first. API base defaults to
    // http://localhost:8000 (override with API_BASE_URL).
    {
      name: 'real-backend',
      testMatch: ['**/real-backend/**', '**/*.realbe.spec.ts'],
      // Use the full chromium build (channel) rather than the headless-shell so
      // a plain `npx playwright install chromium` is sufficient.
      use: { ...devices['Desktop Chrome'], channel: 'chromium' },
    },
  ],

  // Port 5174, NOT the dev default 5173.
  //
  // `reuseExistingServer` attaches to whatever already holds the port — it does
  // not check that the thing listening is the server this config asked for. On
  // a machine running the project's own docker-compose stack, 5173 is bound by
  // the `frontend` container serving a PREVIOUSLY BUILT image, so e2e silently
  // tested a stale bundle instead of local code. That is how a blank-page
  // regression in the production bundle went unnoticed.
  //
  // A dedicated port means the only thing that can be reused is a dev server
  // from an earlier e2e run of this same checkout, which is the intended
  // behaviour. `--strictPort` makes a collision fail loudly rather than let
  // Vite silently pick another port and leave Playwright pointed at nothing.
  webServer: {
    command: 'npm run dev -- --port 5174 --strictPort',
    url: 'http://localhost:5174',
    reuseExistingServer: !process.env.CI,
    timeout: 120_000,
  },
});
