/**
 * Playwright config for REAL E2E tests — no HTTP mocking.
 *
 * Run with:
 *   npx playwright test --config=playwright.real-e2e.config.ts
 *
 * Requires:
 *   - Backend:  cd agent-verse-backend && uv run uvicorn app.main:app
 *   - Frontend: cd agent-verse-frontend && npm run dev
 */

import { defineConfig, devices } from '@playwright/test';

export default defineConfig({
  testDir: './e2e/real-e2e',
  testMatch: '**/*.real.spec.ts',

  // All tests hit the real backend — allow longer timeouts
  timeout: 60_000,
  expect: { timeout: 15_000 },

  // Run serially to avoid tenant creation rate limits
  workers: 2,
  retries: 1,

  reporter: [
    ['list'],
    ['html', { outputFolder: 'playwright-real-e2e-report', open: 'never' }],
  ],

  use: {
    baseURL: process.env.BASE_URL ?? 'http://localhost:5173',
    // No route interception — all traffic reaches the real backend
    bypassCSP: false,
    ignoreHTTPSErrors: false,
    video: 'on-first-retry',
    screenshot: 'only-on-failure',
    trace: 'on-first-retry',

    // Extra HTTP headers for API requests
    extraHTTPHeaders: {
      'X-Test-Run': 'real-e2e',
    },
  },

  projects: [
    {
      name: 'chromium-real-e2e',
      use: { ...devices['Desktop Chrome'] },
    },
  ],

  // Validate backend is up before running
  globalSetup: undefined, // inline health check is done per-test via fixture
});
