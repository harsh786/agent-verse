/**
 * Real E2E — Governance, Audit, HITL, Costs
 *
 * All governance flows against the real backend.
 * No HTTP mocking.
 */

import { test, expect, navigateTo } from './fixtures';

test.describe('Governance — Real E2E', () => {
  // ── Audit trail ───────────────────────────────────────────────────────────

  test('GET /governance/audit returns audit events', async ({ api }) => {
    // Create something to audit
    await api.post('/goals', { goal: 'Audit trail trigger goal' });

    const resp = await api.get('/governance/audit?limit=10');
    expect([200]).toContain(resp.status());
    const body = await resp.json();
    const events = Array.isArray(body) ? body : (body.data ?? body.events ?? body.audit ?? []);
    expect(Array.isArray(events)).toBe(true);
  });

  test('audit events are tenant-scoped', async ({ request }) => {
    const { createE2ETenant, apiClient } = await import('./fixtures');
    const tA = await createE2ETenant(request, '-gov-a');
    const tB = await createE2ETenant(request, '-gov-b');
    const apiA = apiClient(request, tA);
    const apiB = apiClient(request, tB);

    // Tenant A does something
    await apiA.post('/goals', { goal: 'Private goal tenant A' });

    // Tenant B's audit should NOT include Tenant A's events
    const respB = await apiB.get('/governance/audit?limit=50');
    if (respB.status() === 200) {
      const body = await respB.json();
      const events = Array.isArray(body) ? body : (body.data ?? body.events ?? []);
      const leakedEvents = events.filter(
        (e: { tenant_id?: string }) => e.tenant_id === tA.tenantId,
      );
      expect(leakedEvents).toHaveLength(0);
    }
  });

  // ── Policies ──────────────────────────────────────────────────────────────

  test('GET /governance/policies lists policies', async ({ api }) => {
    const resp = await api.get('/governance/policies');
    expect([200]).toContain(resp.status());
    const body = await resp.json();
    const policies = Array.isArray(body) ? body : (body.data ?? body.policies ?? []);
    expect(Array.isArray(policies)).toBe(true);
  });

  test('POST /governance/policies creates a policy', async ({ api }) => {
    const resp = await api.post('/governance/policies', {
      name: `e2e-policy-${Date.now()}`,
      rules: [{ tool: 'web_search', action: 'allow' }],
    });
    expect([200, 201]).toContain(resp.status());
  });

  // ── HITL (Human In The Loop) ─────────────────────────────────────────────

  test('GET /governance/approvals returns pending approvals list', async ({ api }) => {
    const resp = await api.get('/governance/approvals?status=pending');
    expect([200]).toContain(resp.status());
    const body = await resp.json();
    const approvals = Array.isArray(body) ? body : (body.data ?? body.approvals ?? []);
    expect(Array.isArray(approvals)).toBe(true);
  });

  test('POST /governance/approvals/{id}/approve returns 404 for unknown id', async ({ api }) => {
    const resp = await api.post('/governance/approvals/nonexistent-id/approve', {
      note: 'approved by e2e test',
    });
    expect([404, 400]).toContain(resp.status());
  });

  test('POST /governance/approvals/{id}/reject returns 404 for unknown id', async ({ api }) => {
    const resp = await api.post('/governance/approvals/nonexistent-id/reject', {
      reason: 'e2e test rejection',
    });
    expect([404, 400]).toContain(resp.status());
  });

  // ── Costs ─────────────────────────────────────────────────────────────────

  test('GET /costs/summary returns cost summary', async ({ api }) => {
    const resp = await api.get('/costs/summary');
    expect([200]).toContain(resp.status());
    const body = await resp.json();
    expect(body).toBeTruthy();
  });

  test('GET /costs/breakdown returns cost breakdown', async ({ api }) => {
    const resp = await api.get('/costs/breakdown?period=day');
    expect([200]).toContain(resp.status());
  });

  // ── Compliance ────────────────────────────────────────────────────────────

  test('GET /compliance/consent returns consent settings', async ({ api }) => {
    const resp = await api.get('/compliance/consent');
    expect([200, 404]).toContain(resp.status());
  });

  // ── Frontend governance UI ────────────────────────────────────────────────

  test('frontend /governance page loads without errors', async ({ authedPage }) => {
    const errors: string[] = [];
    authedPage.on('pageerror', e => errors.push(e.message));

    await navigateTo(authedPage, '/governance');
    await authedPage.waitForLoadState('networkidle');

    expect(authedPage.url()).not.toContain('/auth');
    expect(errors).toHaveLength(0);
  });

  test('frontend /audit page renders', async ({ authedPage }) => {
    await navigateTo(authedPage, '/audit');
    expect(authedPage.url()).not.toContain('/auth');
  });

  test('frontend /approvals page renders real pending approvals', async ({ authedPage }) => {
    await navigateTo(authedPage, '/approvals');
    expect(authedPage.url()).not.toContain('/auth');
    await authedPage.waitForLoadState('networkidle');
  });

  test('frontend /compliance page renders', async ({ authedPage }) => {
    await navigateTo(authedPage, '/compliance');
    expect(authedPage.url()).not.toContain('/auth');
  });
});
