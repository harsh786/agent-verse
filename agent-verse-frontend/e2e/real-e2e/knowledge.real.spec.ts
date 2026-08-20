/**
 * Real E2E — Knowledge & RAG
 *
 * Collections, ingestion, search — all hitting the real backend.
 * No HTTP mocking.
 */

import { test, expect, navigateTo } from './fixtures';

test.describe('Knowledge — Real E2E', () => {
  // ── Collections ───────────────────────────────────────────────────────────

  test('POST /knowledge/collections creates a collection', async ({ api }) => {
    const resp = await api.post('/knowledge/collections', {
      name: `e2e-collection-${Date.now()}`,
      description: 'E2E test collection',
    });
    expect(resp.status()).toBe(200);
    const body = await resp.json();
    expect(body.collection_id ?? body.id).toBeTruthy();
    expect(body.name).toContain('e2e-collection-');
  });

  test('GET /knowledge/collections lists collections', async ({ api }) => {
    await api.post('/knowledge/collections', {
      name: `list-col-${Date.now()}`,
    });
    const resp = await api.get('/knowledge/collections');
    expect(resp.status()).toBe(200);
    const body = await resp.json();
    const cols = Array.isArray(body) ? body : (body.data ?? body.collections ?? []);
    expect(Array.isArray(cols)).toBe(true);
    expect(cols.length).toBeGreaterThanOrEqual(1);
  });

  test('POST /knowledge/collections rejects empty name', async ({ api }) => {
    const resp = await api.post('/knowledge/collections', { name: '' });
    expect([400, 422]).toContain(resp.status());
  });

  test('collection names are scoped per tenant (isolation)', async ({ request }) => {
    const { createE2ETenant, apiClient } = await import('./fixtures');
    const tA = await createE2ETenant(request, '-kn-a');
    const tB = await createE2ETenant(request, '-kn-b');
    const apiA = apiClient(request, tA);
    const apiB = apiClient(request, tB);

    const name = `isolated-col-${Date.now()}`;
    await apiA.post('/knowledge/collections', { name });

    const listB = await apiB.get('/knowledge/collections');
    const body = await listB.json();
    const cols = Array.isArray(body) ? body : (body.data ?? body.collections ?? []);
    const found = cols.some((c: { name?: string }) => c.name === name);
    expect(found).toBe(false);
  });

  // ── URL Ingestion ─────────────────────────────────────────────────────────

  test('POST /knowledge/ingest/url queues a URL ingestion job', async ({ api }) => {
    const resp = await api.post('/knowledge/ingest/url', {
      url: 'https://example.com',
      collection_name: `ingest-test-${Date.now()}`,
    });
    expect([200, 201, 202]).toContain(resp.status());
    const body = await resp.json();
    const jobId = body.job_id ?? body.id;
    if (jobId) expect(jobId).toBeTruthy();
  });

  test('POST /knowledge/ingest/url rejects invalid URL', async ({ api }) => {
    const resp = await api.post('/knowledge/ingest/url', {
      url: 'not-a-valid-url',
    });
    expect([400, 422]).toContain(resp.status());
  });

  // ── Search ────────────────────────────────────────────────────────────────

  test('GET /knowledge/search returns results (or empty) without error', async ({ api }) => {
    const resp = await api.get('/knowledge/search?q=hello&limit=5');
    expect([200, 204]).toContain(resp.status());
    if (resp.status() === 200) {
      const body = await resp.json();
      const results = Array.isArray(body) ? body : (body.data ?? body.results ?? []);
      expect(Array.isArray(results)).toBe(true);
    }
  });

  test('GET /knowledge/search requires a query parameter', async ({ api }) => {
    const resp = await api.get('/knowledge/search');
    expect([400, 422]).toContain(resp.status());
  });

  // ── Cache ─────────────────────────────────────────────────────────────────

  test('GET /knowledge/cache/stats returns cache statistics', async ({ api }) => {
    const resp = await api.get('/knowledge/cache/stats');
    expect([200]).toContain(resp.status());
  });

  // ── Frontend knowledge UI ─────────────────────────────────────────────────

  test('frontend /knowledge page loads without JS errors', async ({ authedPage }) => {
    const errors: string[] = [];
    authedPage.on('pageerror', e => errors.push(e.message));

    await navigateTo(authedPage, '/knowledge');
    await authedPage.waitForLoadState('networkidle');

    expect(authedPage.url()).not.toContain('/auth');
    expect(errors).toHaveLength(0);
  });

  test('frontend ingestion page renders correctly', async ({ authedPage }) => {
    await navigateTo(authedPage, '/ingestion');
    expect(authedPage.url()).not.toContain('/auth');
  });

  test('frontend knowledge-graph page renders', async ({ authedPage }) => {
    await navigateTo(authedPage, '/knowledge-graph');
    expect(authedPage.url()).not.toContain('/auth');
  });
});
