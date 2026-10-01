/**
 * Playwright config for the REAL-WORLD suite (e2e/real-world): drives the frontend
 * the live Docker stack already serves (default http://localhost:5173) against the
 * live backend (http://localhost:8000). No webServer, no mocking.
 *
 *   REAL_WORLD=1 AGENTVERSE_TENANT_FILE=... npx playwright test --config=playwright.real-world.config.ts
 */
import { defineConfig, devices } from '@playwright/test';

export default defineConfig({
  testDir: './e2e/real-world',
  testMatch: '**/*.spec.ts',
  timeout: 420_000,
  expect: { timeout: 15_000 },
  workers: 1,
  retries: 0,
  reporter: [
    ['list'],
    ['json', { outputFile: process.env.RW_PW_JSON ?? 'test-results/real-world.json' }],
  ],
  use: {
    baseURL: process.env.BASE_URL ?? 'http://localhost:5173',
    screenshot: 'only-on-failure',
    trace: 'off',
    video: 'off',
  },
  projects: [
    {
      name: 'chromium-real-world',
      use: {
        ...devices['Desktop Chrome'],
        launchOptions: process.env.PW_CHROMIUM_EXE
          ? { executablePath: process.env.PW_CHROMIUM_EXE }
          : {},
      },
    },
  ],
});
