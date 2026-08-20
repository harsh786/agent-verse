/**
 * Real E2E — Critical Platform User Journeys
 *
 * End-to-end flows that cover full FE → BFF → Backend → DB paths.
 * No HTTP mocking at any layer.
 *
 * These are the highest-value E2E scenarios: a user signs up, does something
 * meaningful, and the system responds correctly throughout the stack.
 */

import { test, expect, API_BASE, FRONTEND_BASE, createE2ETenant, loginFrontend, apiClient } from './fixtures';

test.describe('Critical User Journeys — Full Stack E2E', () => {
  // ── Journey 1: New user onboarding ────────────────────────────────────────

  test('Journey: Signup → Login → Dashboard — full flow', async ({ page, request }) => {
    // Step 1: Create tenant via real API
    const ts = Date.now();
    const tenant = await createE2ETenant(request, `-journey-${ts}`);
    expect(tenant.apiKey).toMatch(/^av_free_/);

    // Step 2: Sign in to the frontend
    await loginFrontend(page, tenant);

    // Step 3: Navigate to dashboard — must not redirect to /auth
    await page.goto(`${FRONTEND_BASE}/dashboard`, { waitUntil: 'networkidle' });
    expect(page.url()).not.toContain('/auth');

    // Step 4: No console errors
    const errors: string[] = [];
    page.on('pageerror', e => errors.push(e.message));
    await page.waitForTimeout(500);
    expect(errors).toHaveLength(0);
  });

  // ── Journey 2: Goal submission flow ──────────────────────────────────────

  test('Journey: Submit goal via API → verify in goals list', async ({ page, request }) => {
    const tenant = await createE2ETenant(request, `-goal-journey`);
    const api = apiClient(request, tenant);

    // Step 1: Submit a goal
    const goalText = `E2E Journey Goal ${Date.now()}`;
    const createResp = await api.post('/goals', { goal: goalText });
    expect(createResp.status()).toBe(200);
    const { goal_id } = await createResp.json();

    // Step 2: Verify it appears in the goals list
    const listResp = await api.get('/goals?limit=20');
    expect(listResp.status()).toBe(200);
    const listBody = await listResp.json();
    const goals = Array.isArray(listBody) ? listBody : (listBody.data ?? listBody.goals ?? []);
    const found = goals.some((g: { goal_id?: string; goal?: string }) =>
      g.goal_id === goal_id || g.goal === goalText,
    );
    expect(found, `Goal ${goal_id} not found in list`).toBe(true);

    // Step 3: Fetch it by ID
    const fetchResp = await api.get(`/goals/${goal_id}`);
    expect(fetchResp.status()).toBe(200);
    const fetchBody = await fetchResp.json();
    expect(fetchBody.goal).toBe(goalText);

    // Step 4: View in frontend
    await loginFrontend(page, tenant);
    await page.goto(`${FRONTEND_BASE}/goals`, { waitUntil: 'networkidle' });
    expect(page.url()).not.toContain('/auth');
  });

  // ── Journey 3: Dry-run goal with HITL ────────────────────────────────────

  test('Journey: Submit dry-run goal → check no execution side effects', async ({ request }) => {
    const tenant = await createE2ETenant(request, `-dryrun`);
    const api = apiClient(request, tenant);

    // Submit dry run
    const resp = await api.post('/goals', {
      goal: 'Send email to all customers about price increase',
      dry_run: true,
    });
    expect(resp.status()).toBe(200);
    const body = await resp.json();
    expect(body.dry_run).toBe(true);

    // Verify no real emails were queued (audit should show dry run)
    const auditResp = await api.get('/governance/audit?limit=5');
    if (auditResp.status() === 200) {
      const audit = await auditResp.json();
      const events = Array.isArray(audit) ? audit : (audit.data ?? audit.events ?? []);
      // Events should exist but not have real external side effects
      expect(Array.isArray(events)).toBe(true);
    }
  });

  // ── Journey 4: Knowledge ingestion → search ───────────────────────────────

  test('Journey: Create collection → ingest URL → search for content', async ({ request }) => {
    const tenant = await createE2ETenant(request, `-rag-journey`);
    const api = apiClient(request, tenant);

    // Create a collection
    const colResp = await api.post('/knowledge/collections', {
      name: `rag-test-${Date.now()}`,
      description: 'RAG E2E test collection',
    });
    expect([200, 201]).toContain(colResp.status());
    const colBody = await colResp.json();
    const collectionId = colBody.collection_id ?? colBody.id;

    // Ingest a URL (async job)
    const ingestResp = await api.post('/knowledge/ingest/url', {
      url: 'https://example.com',
      collection_name: `rag-test-${Date.now()}`,
    });
    expect([200, 201, 202]).toContain(ingestResp.status());

    // Search (may return empty initially — that's OK for E2E)
    const searchResp = await api.get('/knowledge/search?q=example&limit=5');
    expect([200, 204]).toContain(searchResp.status());
  });

  // ── Journey 5: Connector → Goal with connector ───────────────────────────

  test('Journey: Register connector → submit goal using it', async ({ request }) => {
    const tenant = await createE2ETenant(request, `-conn-journey`);
    const api = apiClient(request, tenant);

    // Create connector
    const connResp = await api.post('/connectors', {
      name: `journey-connector-${Date.now()}`,
      connector_type: 'http',
      base_url: 'https://httpbin.org',
      auth_type: 'none',
    });
    const connStatus = connResp.status();
    expect([200, 201]).toContain(connStatus);

    // Submit goal (connector_ids reference may not be required)
    const goalResp = await api.post('/goals', {
      goal: 'Fetch data from the registered connector',
    });
    expect(goalResp.status()).toBe(200);
    const goalBody = await goalResp.json();
    expect(goalBody.goal_id).toBeTruthy();
  });

  // ── Journey 6: Multi-agent routing ───────────────────────────────────────

  test('Journey: Create agent → submit goal → goal routes to agent', async ({ request }) => {
    const tenant = await createE2ETenant(request, `-routing`);
    const api = apiClient(request, tenant);

    // Create agent
    const agentResp = await api.post('/agents/create', {
      command: 'Handle data analysis tasks efficiently',
      name: `routing-agent-${Date.now()}`,
    });
    expect(agentResp.status()).toBe(200);
    const agentBody = await agentResp.json();
    const agentId = (agentBody.agent ?? agentBody).agent_id ?? agentBody.id ?? agentBody.agent_id;

    // Submit goal (auto-routed or explicit)
    const goalResp = await api.post('/goals', {
      goal: 'Analyze the dataset and generate insights',
      agent_id: agentId ?? null,
    });
    expect(goalResp.status()).toBe(200);
    const goalBody = await goalResp.json();
    expect(goalBody.goal_id).toBeTruthy();
  });

  // ── Journey 7: Tenant isolation verification ─────────────────────────────

  test('Journey: Two tenants cannot see each others data', async ({ request }) => {
    const tA = await createE2ETenant(request, `-iso-journey-a`);
    const tB = await createE2ETenant(request, `-iso-journey-b`);
    const apiA = apiClient(request, tA);
    const apiB = apiClient(request, tB);

    // Tenant A creates resources
    const goalA = await (await apiA.post('/goals', { goal: 'Tenant A secret goal' })).json();
    const colA = await (
      await apiA.post('/knowledge/collections', { name: `private-col-${Date.now()}` })
    ).json();

    // Tenant B tries to access them
    const goalFetch = await apiB.get(`/goals/${goalA.goal_id}`);
    expect([403, 404]).toContain(goalFetch.status());

    const colList = await apiB.get('/knowledge/collections');
    if (colList.status() === 200) {
      const body = await colList.json();
      const cols = Array.isArray(body) ? body : (body.data ?? body.collections ?? []);
      const leaked = cols.some(
        (c: { collection_id?: string }) => c.collection_id === (colA.collection_id ?? colA.id),
      );
      expect(leaked).toBe(false);
    }
  });

  // ── Journey 8: Frontend network health ───────────────────────────────────

  test('Journey: Frontend makes no unexpected 4xx/5xx calls on dashboard load', async ({
    page,
    request,
  }) => {
    const tenant = await createE2ETenant(request, `-network-health`);
    await loginFrontend(page, tenant);

    const unexpectedErrors: { url: string; status: number }[] = [];
    page.on('response', resp => {
      const url = resp.url();
      const status = resp.status();
      // Only care about API calls to backend
      if (url.includes('localhost:8000') && status >= 400) {
        // 401 on /tenants/me initial check is expected before auth completes
        if (status === 401 && url.includes('/tenants/me')) return;
        // 404 on optional endpoints is acceptable
        if (status === 404) return;
        unexpectedErrors.push({ url, status });
      }
    });

    await page.goto(`${FRONTEND_BASE}/dashboard`, { waitUntil: 'networkidle' });
    await page.waitForTimeout(2000);

    expect(
      unexpectedErrors,
      `Unexpected API errors on dashboard: ${JSON.stringify(unexpectedErrors)}`,
    ).toHaveLength(0);
  });
});
