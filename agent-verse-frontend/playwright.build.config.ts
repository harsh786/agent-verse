/**
 * Playwright config that runs the e2e suite against a PRODUCTION BUILD.
 *
 * `playwright.config.ts` starts `npm run dev`, so every e2e run exercises Vite's
 * unbundled ESM dev server. Vitest runs in jsdom with no bundling either. That
 * left an entire class of defect untested: the app shipped with a `manualChunks`
 * configuration that produced a completely BLANK page in the built bundle
 * (`TypeError: Cannot set properties of undefined (setting 'Children')`, then
 * `ReferenceError: Cannot access 'El' before initialization`), and nothing in
 * CI could see it because nothing ever loaded a real build.
 *
 * Usage:
 *   npm run build && npm run test:e2e:build
 */
import baseConfig from './playwright.config';

export default {
  ...baseConfig,
  webServer: {
    command: 'npx vite preview --port 4317 --strictPort',
    url: 'http://localhost:4317',
    reuseExistingServer: !process.env.CI,
    timeout: 120_000,
  },
  use: {
    ...baseConfig.use,
    baseURL: 'http://localhost:4317',
    video: 'off' as const,
    trace: 'off' as const,
  },
};
