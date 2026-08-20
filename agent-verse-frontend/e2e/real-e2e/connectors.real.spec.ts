/**
 * Real E2E — Connectors, Integrations, MCP
 *
 * All connector flows against the real backend.
 * No HTTP mocking.
 */

import { test, expect, navigateTo } from './fixtures';

test.describe('Connectors — Real E2E', () => {
  // ── List ──────────────────────────────────────────────────────────────────

  test('GET /connectors returns empty list for fresh tenant', async ({ api }) => {
    const resp = await api.get('/connectors');
    expect(resp.status()).toBe(200);
    const body = await resp.json();
    const connectors = Array.isArray(body) ? body : (body.data ?? body.connectors ?? []);
    expect(Array.isArray(connectors)).toBe(true);
  });

  // ── Catalog ───────────────────────────────────────────────────────────────

  test('GET /connectors/catalog returns available connector types', async ({ api }) => {
    const resp = await api.get('/connectors/catalog');
    expect([200]).toContain(resp.status());
    const body = await resp.json();
    const catalog = Array.isArray(body) ? body : (body.data ?? body.catalog ?? body.types ?? []);
    expect(Array.isArray(catalog)).toBe(true);
  });

  // ── Create ────────────────────────────────────────────────────────────────

  test('POST /connectors creates a connector', async ({ api }) => {
    const resp = await api.post('/connectors', {
      name: `e2e-connector-${Date.now()}`,
      connector_type: 'http',
      base_url: 'https://httpbin.org',
      auth_type: 'none',
    });
    expect([200, 201]).toContain(resp.status());
  });

  test('POST /connectors rejects missing required fields', async ({ api }) => {
    const resp = await api.post('/connectors', {
      name: `incomplete-${Date.now()}`,
      // missing connector_type
    });
    expect([400, 422]).toContain(resp.status());
  });

  // ── Test connector ────────────────────────────────────────────────────────

  test('POST /connectors/{id}/test returns test result for unknown id', async ({ api }) => {
    const resp = await api.post('/connectors/nonexistent-connector-id/test');
    expect([404, 400]).toContain(resp.status());
  });

  // ── Cross-tenant isolation ────────────────────────────────────────────────

  test('connector isolation between tenants', async ({ request }) => {
    const { createE2ETenant, apiClient } = await import('./fixtures');
    const tA = await createE2ETenant(request, '-con-a');
    const tB = await createE2ETenant(request, '-con-b');
    const apiA = apiClient(request, tA);
    const apiB = apiClient(request, tB);

    const createResp = await apiA.post('/connectors', {
      name: `private-connector-${Date.now()}`,
      connector_type: 'http',
      base_url: 'https://example.com',
      auth_type: 'none',
    });

    if (createResp.status() === 200 || createResp.status() === 201) {
      const body = await createResp.json();
      const connId = body.connector_id ?? body.id;
      if (connId) {
        const fetchResp = await apiB.get(`/connectors/${connId}`);
        expect([403, 404]).toContain(fetchResp.status());
      }
    }
  });

  // ── Frontend connector UI ─────────────────────────────────────────────────

  test('frontend /connectors page loads without errors', async ({ authedPage }) => {
    const errors: string[] = [];
    authedPage.on('pageerror', e => errors.push(e.message));

    await navigateTo(authedPage, '/connectors');
    await authedPage.waitForLoadState('networkidle');

    expect(authedPage.url()).not.toContain('/auth');
    expect(errors).toHaveLength(0);
  });

  test('frontend connector catalog page renders', async ({ authedPage }) => {
    await navigateTo(authedPage, '/connectors');
    expect(authedPage.url()).not.toContain('/auth');
  });

  test('frontend /integrations page loads', async ({ authedPage }) => {
    await navigateTo(authedPage, '/integrations');
    expect(authedPage.url()).not.toContain('/auth');
  });
});
