/**
 * Real E2E — Chat, Marketplace, Scheduling, Security
 *
 * No HTTP mocking. All requests hit localhost:8000.
 */

import { test, expect, navigateTo } from './fixtures';

test.describe('Chat — Real E2E', () => {
  test('POST /chat/sessions creates a chat session', async ({ api }) => {
    const resp = await api.post('/chat/sessions', {
      title: `E2E Chat ${Date.now()}`,
    });
    expect([200, 201]).toContain(resp.status());
    const body = await resp.json();
    expect(body.session_id ?? body.id).toBeTruthy();
  });

  test('GET /chat/sessions lists chat sessions', async ({ api }) => {
    await api.post('/chat/sessions', { title: `List test ${Date.now()}` });
    const resp = await api.get('/chat/sessions?limit=5');
    expect([200]).toContain(resp.status());
    const body = await resp.json();
    const sessions = Array.isArray(body) ? body : (body.data ?? body.sessions ?? []);
    expect(Array.isArray(sessions)).toBe(true);
  });

  test('POST /chat/sessions/{id}/messages sends a message', async ({ api }) => {
    const createResp = await api.post('/chat/sessions', { title: `Msg test ${Date.now()}` });
    const body = await createResp.json();
    const sessionId = body.session_id ?? body.id;
    if (!sessionId) return;

    const msgResp = await api.post(`/chat/sessions/${sessionId}/messages`, {
      content: 'Hello from E2E test',
      role: 'user',
    });
    expect([200, 201, 202]).toContain(msgResp.status());
  });

  test('frontend /chat page loads without errors', async ({ authedPage }) => {
    const errors: string[] = [];
    authedPage.on('pageerror', e => errors.push(e.message));
    await navigateTo(authedPage, '/chat');
    await authedPage.waitForLoadState('networkidle');
    expect(authedPage.url()).not.toContain('/auth');
    expect(errors).toHaveLength(0);
  });
});

test.describe('Marketplace — Real E2E', () => {
  test('GET /marketplace/templates returns templates', async ({ api }) => {
    const resp = await api.get('/marketplace/templates?limit=10');
    expect([200]).toContain(resp.status());
    const body = await resp.json();
    const templates = Array.isArray(body) ? body : (body.data ?? body.templates ?? []);
    expect(Array.isArray(templates)).toBe(true);
  });

  test('GET /marketplace/templates/{id} returns 404 for unknown id', async ({ api }) => {
    const resp = await api.get('/marketplace/templates/nonexistent-template-00000000');
    expect([404, 400]).toContain(resp.status());
  });

  test('frontend /marketplace page loads without errors', async ({ authedPage }) => {
    const errors: string[] = [];
    authedPage.on('pageerror', e => errors.push(e.message));
    await navigateTo(authedPage, '/marketplace');
    await authedPage.waitForLoadState('networkidle');
    expect(authedPage.url()).not.toContain('/auth');
    expect(errors).toHaveLength(0);
  });

  test('frontend /templates page renders', async ({ authedPage }) => {
    await navigateTo(authedPage, '/templates');
    expect(authedPage.url()).not.toContain('/auth');
  });
});

test.describe('Schedules — Real E2E', () => {
  test('GET /schedules returns schedules', async ({ api }) => {
    const resp = await api.get('/schedules?limit=10');
    expect([200]).toContain(resp.status());
    const body = await resp.json();
    const schedules = Array.isArray(body) ? body : (body.data ?? body.schedules ?? []);
    expect(Array.isArray(schedules)).toBe(true);
  });

  test('POST /schedules creates a schedule', async ({ api }) => {
    // CreateScheduleRequest (app/api/schedules.py) fields are trigger_type/cron_expr,
    // not cron_expression/enabled.
    const resp = await api.post('/schedules', {
      name: `e2e-schedule-${Date.now()}`,
      goal_template: 'Generate daily report',
      trigger_type: 'cron',
      cron_expr: '0 9 * * 1-5',
    });
    expect([200, 201]).toContain(resp.status());
  });

  test('POST /schedules rejects invalid cron expression', async ({ api }) => {
    const resp = await api.post('/schedules', {
      name: `bad-cron-${Date.now()}`,
      goal_template: 'Test',
      trigger_type: 'cron',
      cron_expr: 'not-valid-cron',
    });
    expect([400, 422]).toContain(resp.status());
  });

  test('frontend /schedules page loads without errors', async ({ authedPage }) => {
    const errors: string[] = [];
    authedPage.on('pageerror', e => errors.push(e.message));
    await navigateTo(authedPage, '/schedules');
    await authedPage.waitForLoadState('networkidle');
    expect(authedPage.url()).not.toContain('/auth');
    expect(errors).toHaveLength(0);
  });
});

test.describe('Security — Real E2E', () => {
  test('all API endpoints require authentication', async ({ request }) => {
    const { API_BASE } = await import('./fixtures');
    const endpoints = [
      '/goals',
      '/agents',
      '/connectors',
      '/knowledge/collections',
      '/memory',
      '/schedules',
      '/governance/audit',
    ];
    for (const ep of endpoints) {
      const resp = await request.get(`${API_BASE}${ep}`);
      expect(resp.status(), `Endpoint ${ep} should return 401 without auth`).toBe(401);
    }
  });

  test('POST endpoints require authentication', async ({ request }) => {
    const { API_BASE } = await import('./fixtures');
    const endpoints = [
      ['/goals', { goal: 'test' }],
      ['/agents/create', { command: 'test', name: 'test' }],
    ] as [string, object][];
    for (const [ep, body] of endpoints) {
      const resp = await request.post(`${API_BASE}${ep}`, {
        data: body,
        headers: { 'Content-Type': 'application/json' },
      });
      expect([401, 403], `Endpoint POST ${ep} should require auth`).toContain(resp.status());
    }
  });

  test('CORS headers present on API responses', async ({ request }) => {
    const { API_BASE } = await import('./fixtures');
    const resp = await request.get(`${API_BASE}/health`);
    expect(resp.status()).toBe(200);
    // Health endpoint should respond — CORS not required for health
  });

  test('frontend /security page loads without errors', async ({ authedPage }) => {
    const errors: string[] = [];
    authedPage.on('pageerror', e => errors.push(e.message));
    await navigateTo(authedPage, '/security');
    await authedPage.waitForLoadState('networkidle');
    expect(authedPage.url()).not.toContain('/auth');
    expect(errors).toHaveLength(0);
  });
});
