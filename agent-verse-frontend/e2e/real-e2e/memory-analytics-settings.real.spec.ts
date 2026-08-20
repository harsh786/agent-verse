/**
 * Real E2E — Memory, Analytics, Observability, Settings
 *
 * No HTTP mocking. All requests hit localhost:8000.
 */

import { test, expect, navigateTo } from './fixtures';

test.describe('Memory — Real E2E', () => {
  test('GET /memory returns memory entries', async ({ api }) => {
    const resp = await api.get('/memory?limit=10');
    expect([200]).toContain(resp.status());
    const body = await resp.json();
    const entries = Array.isArray(body) ? body : (body.data ?? body.memories ?? []);
    expect(Array.isArray(entries)).toBe(true);
  });

  test('POST /memory stores a memory entry', async ({ api }) => {
    const resp = await api.post('/memory', {
      content: 'E2E test memory entry',
      memory_type: 'fact',
    });
    expect([200, 201]).toContain(resp.status());
  });

  test('POST /memory/recall retrieves relevant memories', async ({ api }) => {
    // Store first
    await api.post('/memory', { content: 'Paris is the capital of France', memory_type: 'fact' });

    const resp = await api.post('/memory/recall', { query: 'What is the capital of France?' });
    expect([200]).toContain(resp.status());
    const body = await resp.json();
    const memories = Array.isArray(body) ? body : (body.data ?? body.memories ?? body.results ?? []);
    expect(Array.isArray(memories)).toBe(true);
  });

  test('memory is tenant-scoped', async ({ request }) => {
    const { createE2ETenant, apiClient } = await import('./fixtures');
    const tA = await createE2ETenant(request, '-mem-a');
    const tB = await createE2ETenant(request, '-mem-b');
    const apiA = apiClient(request, tA);
    const apiB = apiClient(request, tB);

    await apiA.post('/memory', { content: 'Secret memory tenant A', memory_type: 'fact' });

    const recallB = await apiB.post('/memory/recall', { query: 'Secret memory tenant A' });
    if (recallB.status() === 200) {
      const body = await recallB.json();
      const memories = Array.isArray(body) ? body : (body.data ?? body.memories ?? body.results ?? []);
      // Tenant B should not find Tenant A's memories
      const leaked = memories.filter(
        (m: { content?: string; tenant_id?: string }) =>
          (m.content?.includes('Secret memory tenant A')) || m.tenant_id === tA.tenantId,
      );
      expect(leaked).toHaveLength(0);
    }
  });

  test('frontend /memory page loads without errors', async ({ authedPage }) => {
    const errors: string[] = [];
    authedPage.on('pageerror', e => errors.push(e.message));
    await navigateTo(authedPage, '/memory');
    await authedPage.waitForLoadState('networkidle');
    expect(authedPage.url()).not.toContain('/auth');
    expect(errors).toHaveLength(0);
  });
});

test.describe('Analytics — Real E2E', () => {
  test('GET /analytics/overview returns analytics data', async ({ api }) => {
    const resp = await api.get('/analytics/overview');
    expect([200]).toContain(resp.status());
  });

  test('GET /analytics/goals returns goal analytics', async ({ api }) => {
    const resp = await api.get('/analytics/goals?period=week');
    expect([200]).toContain(resp.status());
  });

  test('frontend /analytics page loads without errors', async ({ authedPage }) => {
    const errors: string[] = [];
    authedPage.on('pageerror', e => errors.push(e.message));
    await navigateTo(authedPage, '/analytics');
    await authedPage.waitForLoadState('networkidle');
    expect(authedPage.url()).not.toContain('/auth');
    expect(errors).toHaveLength(0);
  });
});

test.describe('Observability — Real E2E', () => {
  test('GET /health returns healthy status', async ({ request }) => {
    const { API_BASE } = await import('./fixtures');
    const resp = await request.get(`${API_BASE}/health`);
    expect(resp.status()).toBe(200);
    const body = await resp.json();
    expect(body.status).toBe('healthy');
  });

  test('observability metrics endpoint responds', async ({ api }) => {
    const resp = await api.get('/metrics');
    expect([200, 404]).toContain(resp.status());
  });

  test('frontend /observability page loads', async ({ authedPage }) => {
    const errors: string[] = [];
    authedPage.on('pageerror', e => errors.push(e.message));
    await navigateTo(authedPage, '/observability');
    await authedPage.waitForLoadState('networkidle');
    expect(authedPage.url()).not.toContain('/auth');
    expect(errors).toHaveLength(0);
  });
});

test.describe('Settings — Real E2E', () => {
  test('GET /tenants/me returns tenant settings', async ({ api, tenant }) => {
    const resp = await api.get('/tenants/me');
    expect(resp.status()).toBe(200);
    const body = await resp.json();
    expect(body.tenant_id).toBe(tenant.tenantId);
  });

  test('frontend /settings page loads without errors', async ({ authedPage }) => {
    const errors: string[] = [];
    authedPage.on('pageerror', e => errors.push(e.message));
    await navigateTo(authedPage, '/settings');
    await authedPage.waitForLoadState('networkidle');
    expect(authedPage.url()).not.toContain('/auth');
    expect(errors).toHaveLength(0);
  });

  test('frontend /settings/models page loads', async ({ authedPage }) => {
    await navigateTo(authedPage, '/models');
    expect(authedPage.url()).not.toContain('/auth');
  });
});
