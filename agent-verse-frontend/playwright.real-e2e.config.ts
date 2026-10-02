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
  // Everything in this directory is a real-backend test, whatever it is named.
  // The previous '**/*.real.spec.ts' missed differently-named siblings such as
  // all-features-real.spec.ts (64 tests) and real-e2e-no-mock.spec.ts (28),
  // which would have belonged to no config at all once the mocked projects
  // stopped sweeping them in.
  testMatch: '**/*.spec.ts',

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
    baseURL:
      process.env.BASE_URL ??
      (process.env.PW_START_WEB_SERVER ? 'http://localhost:5174' : 'http://localhost:5173'),
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
      use: {
        ...devices['Desktop Chrome'],
        // Use the already-installed chromium build (no extra download).
        launchOptions: process.env.PW_CHROMIUM_EXE
          ? { executablePath: process.env.PW_CHROMIUM_EXE }
          : {},
      },
    },
  ],

  // Validate backend is up before running
  globalSetup: undefined, // inline health check is done per-test via fixture

  // CI (and anyone without a dev server running) sets PW_START_WEB_SERVER=1 to
  // have Playwright start Vite itself — on 5174 with --strictPort, never
  // attaching to whatever holds 5173 (the compose frontend's stale bundle). Set
  // BASE_URL=http://localhost:5174 alongside it.
  webServer: process.env.PW_START_WEB_SERVER
    ? {
        command: 'npm run dev -- --port 5174 --strictPort --mode e2e',
        url: 'http://localhost:5174',
        reuseExistingServer: false,
        timeout: 120_000,
      }
    : undefined,
});
