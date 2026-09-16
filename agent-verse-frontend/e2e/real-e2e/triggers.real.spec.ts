/**
 * Real E2E tests for Triggers — NO HTTP mocking.
 *
 * Route: /triggers (src/app/App.tsx) → src/features/triggers/TriggersPage.tsx
 * Backend: app/api/triggers.py, mounted with prefix "/triggers"
 *   GET  /triggers            List triggers for the tenant
 *   POST /triggers            Create a trigger
 *   GET  /triggers/dlq        Dead-letter queue entries
 *   GET  /triggers/{id}       Get a trigger
 *   POST /triggers/{id}/pause / /resume / /simulate / /fire
 *
 * Every request goes through: browser → localhost:5173 (Vite) → localhost:8000 (FastAPI).
 */

import { expect } from '@playwright/test';
import { test, FRONTEND_BASE } from './fixtures';

test.describe('Triggers — real API', () => {
  test('triggers list returns an empty array for a new tenant', async ({ api }) => {
    const resp = await api.get('/triggers');
    expect(resp.status()).toBe(200);
    const body = await resp.json();
    expect(Array.isArray(body)).toBe(true);
    expect(body.length).toBe(0);
  });

  test('create an interval trigger via real API, then see it in the list', async ({ api }) => {
    const createResp = await api.post('/triggers', {
      spec: {
        trigger_type: 'interval',
        name: 'e2e-real-interval-trigger',
        interval_seconds: 3600,
      },
      goal_template: 'Say hello to {{payload.name}}',
    });
    expect(createResp.status()).toBe(201);
    const created = await createResp.json();
    expect(created.schedule_id).toBeTruthy();
    expect(created.paused).toBe(false);
    expect(created.spec.trigger_type).toBe('interval');

    const listResp = await api.get('/triggers');
    expect(listResp.status()).toBe(200);
    const list = await listResp.json();
    expect(list.some((t: { schedule_id: string }) => t.schedule_id === created.schedule_id)).toBe(true);
  });

  test('create trigger without goal_id, goal_template, or agent_id is rejected (422)', async ({ api }) => {
    const resp = await api.post('/triggers', {
      spec: { trigger_type: 'interval', interval_seconds: 60 },
    });
    expect(resp.status()).toBe(422);
  });

  test('pause and resume a real trigger via the API', async ({ api }) => {
    const createResp = await api.post('/triggers', {
      spec: { trigger_type: 'interval', name: 'e2e-pause-resume', interval_seconds: 120 },
      goal_template: 'Ping',
    });
    expect(createResp.status()).toBe(201);
    const created = await createResp.json();
    const id = created.schedule_id;

    const pauseResp = await api.post(`/triggers/${id}/pause`);
    expect(pauseResp.status()).toBe(200);
    const paused = await pauseResp.json();
    expect(paused.paused).toBe(true);

    const resumeResp = await api.post(`/triggers/${id}/resume`);
    expect(resumeResp.status()).toBe(200);
    const resumed = await resumeResp.json();
    expect(resumed.paused).toBe(false);
  });

  test('dead-letter queue endpoint returns an array', async ({ api }) => {
    const resp = await api.get('/triggers/dlq');
    expect(resp.status()).toBe(200);
    const body = await resp.json();
    expect(Array.isArray(body)).toBe(true);
  });
});

test.describe('Triggers — real page', () => {
  test('triggers page renders without a critical error', async ({ authedPage }) => {
    await authedPage.goto(`${FRONTEND_BASE}/triggers`, { waitUntil: 'networkidle' });
    await expect(authedPage.locator('body')).toBeVisible();
    const text = (await authedPage.locator('body').textContent()) ?? '';
    expect(text.toLowerCase()).not.toContain('internal server error');
    expect(text).toMatch(/Triggers/i);
  });

  test('triggers page shows a created trigger in the list', async ({ authedPage, api }) => {
    const createResp = await api.post('/triggers', {
      spec: { trigger_type: 'interval', name: 'e2e-page-visible-trigger', interval_seconds: 300 },
      goal_template: 'Say hi',
    });
    expect(createResp.status()).toBe(201);

    await authedPage.goto(`${FRONTEND_BASE}/triggers`, { waitUntil: 'networkidle' });
    const text = (await authedPage.locator('body').textContent()) ?? '';
    expect(text).toMatch(/e2e-page-visible-trigger|interval/i);
  });

  test('switching to the Dead Letter Queue tab renders without crashing', async ({ authedPage }) => {
    await authedPage.goto(`${FRONTEND_BASE}/triggers`, { waitUntil: 'networkidle' });
    await authedPage.getByText('Dead Letter Queue').click();
    await expect(authedPage.locator('body')).toBeVisible();
    const text = (await authedPage.locator('body').textContent()) ?? '';
    expect(text.toLowerCase()).not.toContain('internal server error');
  });

  test('opening the New Trigger modal renders without crashing', async ({ authedPage }) => {
    await authedPage.goto(`${FRONTEND_BASE}/triggers`, { waitUntil: 'networkidle' });
    await authedPage.getByText('New Trigger').click();
    await expect(authedPage.locator('body')).toBeVisible();
  });
});
