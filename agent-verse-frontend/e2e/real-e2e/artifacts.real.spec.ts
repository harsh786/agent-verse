/**
 * Real E2E tests for the Artifacts browser feature — NO HTTP mocking.
 *
 * Route: /artifacts → src/features/artifacts/ArtifactsBrowserPage.tsx
 * Backend: agent-verse-backend/app/api/artifacts.py (prefix "/artifacts").
 *   - GET  /artifacts               (list, paginated — the page calls this via
 *                                    artifactsApi.list() in src/lib/api/client.ts)
 *   - GET  /artifacts/{artifact_id} (single artifact)
 *
 * Run:
 *   npx playwright test --config=playwright.real-e2e.config.ts e2e/real-e2e/artifacts.real.spec.ts
 */

import { expect } from '@playwright/test';
import { test, FRONTEND_BASE } from './fixtures';

test.describe('Artifacts — real artifact browsing', () => {
  test('artifacts list API returns data for a fresh tenant', async ({ api }) => {
    const resp = await api.get('/artifacts?limit=50');
    expect(resp.status()).toBe(200);
    const body = await resp.json();
    // Backend may return either a bare array or a paginated object — accept both,
    // matching the ArtifactListResponse union in src/lib/api/client.ts.
    const items = Array.isArray(body) ? body : (body.items ?? body.artifacts ?? body.data);
    expect(Array.isArray(items)).toBe(true);
  });

  test('unknown artifact id returns 404, not a crash', async ({ api }) => {
    const resp = await api.get('/artifacts/does-not-exist-e2e');
    expect([404, 400]).toContain(resp.status());
  });

  test('artifacts page renders without a critical error', async ({ authedPage }) => {
    await authedPage.goto(`${FRONTEND_BASE}/artifacts`, { waitUntil: 'networkidle' });
    await expect(authedPage.locator('body')).toBeVisible();
    const text = (await authedPage.locator('body').textContent()) ?? '';
    expect(text.toLowerCase()).not.toContain('internal server error');
    expect(text.length).toBeGreaterThan(0);
  });
});
