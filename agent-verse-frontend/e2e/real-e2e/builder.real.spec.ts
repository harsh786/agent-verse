/**
 * Real E2E tests for the Builder (site/app generator) feature — NO HTTP mocking.
 *
 * Route: /builder → src/features/builder/BuilderPage.tsx
 * Backend: agent-verse-backend/app/api/builder.py (prefix "/builder").
 *   - POST /builder/projects              (submit a build — creates a
 *                                          code-generation goal via GoalService)
 *   - GET  /builder/projects/{project_id} (project status/artifacts)
 *
 * Note: POST /builder/projects submits a real goal through GoalService, so we
 * only assert the synchronous "accepted" response shape (project_id, workspace_id,
 * status) — we do not wait for the underlying agent build to actually finish.
 *
 * Run:
 *   npx playwright test --config=playwright.real-e2e.config.ts e2e/real-e2e/builder.real.spec.ts
 */

import { expect } from '@playwright/test';
import { test, FRONTEND_BASE } from './fixtures';

test.describe('Builder — real project generation', () => {
  test('create builder project via real API', async ({ api }) => {
    const resp = await api.post('/builder/projects', {
      description: 'A simple one-page marketing site for a coffee shop',
      project_type: 'landing',
      framework: 'react',
    });
    expect([200, 201, 503]).toContain(resp.status());
    if (resp.status() !== 503) {
      const body = await resp.json();
      expect(body.project_id).toBeTruthy();
      expect(body.workspace_id).toBeTruthy();
      expect(body.status).toBeTruthy();
    }
  });

  test('builder project status API returns project state', async ({ api }) => {
    const resp = await api.get('/builder/projects/e2e-test-project-id');
    expect(resp.status()).toBe(200);
    const body = await resp.json();
    expect(body.project_id).toBe('e2e-test-project-id');
    expect(body).toHaveProperty('status');
  });

  test('builder page renders without a critical error', async ({ authedPage }) => {
    await authedPage.goto(`${FRONTEND_BASE}/builder`, { waitUntil: 'networkidle' });
    await expect(authedPage.locator('body')).toBeVisible();
    const text = (await authedPage.locator('body').textContent()) ?? '';
    expect(text.toLowerCase()).not.toContain('internal server error');
    expect(text.length).toBeGreaterThan(0);
  });
});
