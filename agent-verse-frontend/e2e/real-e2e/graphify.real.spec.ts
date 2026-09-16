/**
 * Real E2E tests for Graphify — NO HTTP mocking.
 *
 * Route: /graphify, page src/features/graphify/GraphifyPage.tsx.
 * Backend: agent-verse-backend/app/org/router.py — GraphifyPage lists orgs via
 * GET /v1/org (CursorPage<OrganizationResponse>) and, once one is selected,
 * would kick off POST /v1/org/{org_id}/graphify (a real, potentially
 * long-running knowledge-graph build). We exercise the real org-listing round
 * trip and the page's empty/populated states, but stop short of starting a
 * real graphify build (which streams over SSE and can take a while against a
 * real LLM/knowledge base) to keep this test fast and deterministic.
 */
import { expect } from '@playwright/test';
import { test, FRONTEND_BASE } from './fixtures';

test.describe('Graphify — real backend', () => {
  test('GET /v1/org returns an empty cursor page for a fresh tenant', async ({ api }) => {
    const resp = await api.get('/v1/org');
    expect(resp.status()).toBe(200);
    const body = await resp.json();
    expect(Array.isArray(body.data)).toBe(true);
    expect(body.data.length).toBe(0);
  });

  test('graphify page shows the "no organisations" state for a fresh tenant', async ({ authedPage }) => {
    await authedPage.goto(`${FRONTEND_BASE}/graphify`, { waitUntil: 'networkidle' });

    await expect(authedPage.getByRole('heading', { name: 'Graphify' })).toBeVisible({ timeout: 10000 });
    await expect(authedPage.getByText('No organisations found.')).toBeVisible({ timeout: 10000 });
  });

  test('creating a real org via the API surfaces it as a selectable org on the graphify page', async ({ api, authedPage }) => {
    const createResp = await api.post('/v1/org', { name: `Graphify E2E Org ${Date.now()}` });
    expect([200, 201]).toContain(createResp.status());
    const org = await createResp.json();
    expect(org.id ?? org.org_id).toBeTruthy();

    await authedPage.goto(`${FRONTEND_BASE}/graphify`, { waitUntil: 'networkidle' });
    await expect(authedPage.getByRole('heading', { name: 'Graphify' })).toBeVisible({ timeout: 10000 });

    // The org auto-selects (first org), revealing the "Start Graphify" action.
    await expect(authedPage.getByRole('button', { name: 'Start Graphify' })).toBeVisible({ timeout: 10000 });
  });
});
