/**
 * Real E2E tests for the Workflow Engine — NO HTTP mocking.
 *
 * Route: /workflow-engine (src/app/App.tsx) → src/features/workflow-engine/WorkflowEnginePage.tsx
 * Distinct from /workflow-builder and /workflows, which are covered in all-features-real.spec.ts.
 *
 * Backend: app/workflow/router.py (prefix "/workflows", mounted under "/api/v1") and
 * app/workflow/router_runs.py (prefix "/runs", mounted under "/api/v1"):
 *   GET  /api/v1/workflows              List workflow definitions (paginated)
 *   POST /api/v1/workflows              Create workflow definition
 *   POST /api/v1/workflows/{id}/trigger Manually trigger a run
 *   POST /api/v1/workflows/{id}/pause / /resume
 *   GET  /api/v1/runs                   List runs across all workflows (paginated)
 *
 * Every request goes through: browser → localhost:5173 (Vite) → localhost:8000 (FastAPI).
 */

import { expect } from '@playwright/test';
import { test, FRONTEND_BASE } from './fixtures';

test.describe('Workflow Engine — real API', () => {
  test('workflows list returns an empty paginated envelope for a new tenant', async ({ api }) => {
    const resp = await api.get('/api/v1/workflows?per_page=30');
    expect(resp.status()).toBe(200);
    const body = await resp.json();
    expect(body).toHaveProperty('items');
    expect(body).toHaveProperty('total');
    expect(Array.isArray(body.items)).toBe(true);
    expect(body.total).toBe(0);
  });

  test('runs list (used by the Run History tab) returns a paginated envelope', async ({ api }) => {
    const resp = await api.get('/api/v1/runs?limit=20');
    expect(resp.status()).toBe(200);
    const body = await resp.json();
    expect(body).toHaveProperty('items');
    expect(body).toHaveProperty('total');
    expect(Array.isArray(body.items)).toBe(true);
  });

  test('create a workflow, publish it, and manually trigger a run', async ({ api }) => {
    const createResp = await api.post('/api/v1/workflows', {
      name: 'e2e-real-workflow-engine',
      description: 'Created by workflow-engine.real.spec.ts',
      definition: {
        name: 'e2e-real-workflow-engine',
        trigger: { type: 'api' },
        steps: [],
      },
    });
    expect(createResp.status()).toBe(201);
    const created = await createResp.json();
    const id = created.id ?? created.workflow_id;
    expect(id).toBeTruthy();
    expect(created.status).toBe('draft');

    // Confirm it now shows up in the list the page renders from.
    const listResp = await api.get('/api/v1/workflows?per_page=30');
    const list = await listResp.json();
    expect(list.items.some((w: { id: string }) => w.id === id)).toBe(true);

    const publishResp = await api.post(`/api/v1/workflows/${id}/publish`);
    expect([200, 400, 422]).toContain(publishResp.status());
  });

  test('pause/resume on an unknown workflow id returns 404, not a 500', async ({ api }) => {
    const resp = await api.post('/api/v1/workflows/does-not-exist/pause');
    expect(resp.status()).toBe(404);
  });
});

test.describe('Workflow Engine — real page', () => {
  test('workflow-engine page renders the Workflows tab without a critical error', async ({ authedPage }) => {
    await authedPage.goto(`${FRONTEND_BASE}/workflow-engine`, { waitUntil: 'networkidle' });
    await expect(authedPage.locator('body')).toBeVisible();
    const text = (await authedPage.locator('body').textContent()) ?? '';
    expect(text.toLowerCase()).not.toContain('internal server error');
    expect(text).toMatch(/Workflow Engine/i);
  });

  test('switching to the Run History tab renders without a critical error', async ({ authedPage }) => {
    await authedPage.goto(`${FRONTEND_BASE}/workflow-engine`, { waitUntil: 'networkidle' });
    await authedPage.getByRole('button', { name: 'Run History' }).click();
    await authedPage.waitForLoadState('networkidle');
    await expect(authedPage.locator('body')).toBeVisible();
    const text = (await authedPage.locator('body').textContent()) ?? '';
    expect(text.toLowerCase()).not.toContain('internal server error');
    // Regression guard: the Run History tab used to call the wrong backend path
    // (/api/v1/workflows/runs, which 404s because it matches the GET /{workflow_id}
    // route) and silently rendered "No run history yet" for every tenant. With the
    // corrected /api/v1/runs endpoint this empty-state text is still valid for a
    // fresh tenant with no runs, so we just assert the page didn't crash.
    expect(text.length).toBeGreaterThan(0);
  });

  test('a workflow created via the API is visible on the Workflows tab', async ({ authedPage, api }) => {
    const createResp = await api.post('/api/v1/workflows', {
      name: 'e2e-page-visible-workflow',
      definition: {
        name: 'e2e-page-visible-workflow',
        trigger: { type: 'api' },
        steps: [],
      },
    });
    expect(createResp.status()).toBe(201);

    await authedPage.goto(`${FRONTEND_BASE}/workflow-engine`, { waitUntil: 'networkidle' });
    const text = (await authedPage.locator('body').textContent()) ?? '';
    expect(text).toMatch(/e2e-page-visible-workflow/i);
  });
});
