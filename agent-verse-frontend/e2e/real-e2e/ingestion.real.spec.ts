/**
 * Real E2E tests for Knowledge Sources (Ingestion) — NO HTTP mocking.
 *
 * Route: /sources, page src/features/ingestion/SourcesPage.tsx.
 * Backend: agent-verse-backend/app/api/ingestion.py — `router` (prefix /sources)
 * and `documents_router` (prefix /ingestion, includes /ingestion/quota).
 *
 * The mocked suite (e2e/ingestion.spec.ts) exercises page load, empty state,
 * populated state (via mocked source data) and the full create-source wizard
 * (family -> type -> configure -> submit) against a mocked postgresql source.
 *
 * Against the real backend we mirror the read-only parts (list sources, empty
 * state, family filter chips) for a real fresh tenant. Completing the create
 * wizard against a real external connector (e.g. an actual Postgres/S3/Slack
 * endpoint) needs real credentials this environment doesn't have, so the
 * create-flow test here only opens the wizard and verifies family -> type ->
 * configure routing renders (FamilyFormRouter), without submitting.
 */
import { expect } from '@playwright/test';
import { test, FRONTEND_BASE } from './fixtures';

test.describe('Ingestion / Sources — real backend', () => {
  test('GET /sources returns an empty list for a fresh tenant', async ({ api }) => {
    const resp = await api.get('/sources');
    expect(resp.status()).toBe(200);
    const body = await resp.json();
    expect(Array.isArray(body)).toBe(true);
    expect(body.length).toBe(0);
  });

  test('GET /ingestion/quota returns real quota data (or 404 if unset)', async ({ api }) => {
    const resp = await api.get('/ingestion/quota');
    expect([200, 404]).toContain(resp.status());
  });

  test('sources page renders header, stat cards, and family filter chips', async ({ authedPage }) => {
    await authedPage.goto(`${FRONTEND_BASE}/sources`, { waitUntil: 'networkidle' });

    await expect(authedPage.getByRole('heading', { name: 'Knowledge Sources' })).toBeVisible({ timeout: 10000 });
    await expect(authedPage.getByText('Total Sources')).toBeVisible();
    await expect(authedPage.getByText('Active')).toBeVisible();
    await expect(authedPage.getByText('Families')).toBeVisible();

    await expect(authedPage.getByRole('checkbox', { name: 'All' })).toBeVisible();
    await expect(authedPage.getByRole('checkbox', { name: 'Object Storage', exact: false })).toBeVisible();
  });

  test('sources page shows the real empty state for a fresh tenant', async ({ authedPage }) => {
    await authedPage.goto(`${FRONTEND_BASE}/sources`, { waitUntil: 'networkidle' });

    await expect(authedPage.getByRole('heading', { name: 'Knowledge Sources' })).toBeVisible({ timeout: 10000 });
    await expect(authedPage.getByText('No knowledge sources yet')).toBeVisible({ timeout: 5000 });
    await expect(authedPage.getByRole('button', { name: 'Add your first source' })).toBeVisible();
  });

  test('create-source wizard opens and routes family -> type -> configure form (no submit)', async ({ authedPage, api }) => {
    await authedPage.goto(`${FRONTEND_BASE}/sources`, { waitUntil: 'networkidle' });
    await expect(authedPage.getByRole('heading', { name: 'Knowledge Sources' })).toBeVisible({ timeout: 10000 });

    await authedPage.getByRole('button', { name: 'Add Source' }).click();

    const dialog = authedPage.getByRole('dialog', { name: 'Add knowledge source' });
    await expect(dialog).toBeVisible({ timeout: 5000 });

    // Step 1: family
    await expect(dialog.getByText('Choose a source family')).toBeVisible();
    await dialog.getByText('Relational DB', { exact: true }).click();

    // Step 2: type
    await expect(dialog.getByText('postgresql', { exact: true })).toBeVisible({ timeout: 5000 });
    await dialog.getByText('postgresql', { exact: true }).click();

    // Step 3: configure — verify the family-specific connection form (FamilyFormRouter) renders.
    await expect(dialog.getByText('Source Name *')).toBeVisible({ timeout: 5000 });
    await expect(dialog.getByText('Host')).toBeVisible();
    await expect(dialog.getByText('CDC Mode')).toBeVisible();

    const submitBtn = dialog.getByRole('button', { name: /create source/i });
    await expect(submitBtn).toBeDisabled();

    // Close without submitting — no real external connector credentials available here.
    await dialog.getByRole('button', { name: 'Close' }).click();
    await expect(dialog).toHaveCount(0);

    // No source was actually created against the real backend.
    const resp = await api.get('/sources');
    const sources = await resp.json();
    expect(Array.isArray(sources)).toBe(true);
    expect(sources.length).toBe(0);
  });
});
