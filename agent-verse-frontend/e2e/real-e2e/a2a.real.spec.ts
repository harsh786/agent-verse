/**
 * Real E2E tests for the A2A (Agent-to-Agent protocol) feature — NO HTTP mocking.
 *
 * Route: /a2a → src/features/a2a/A2APage.tsx
 * Backend: agent-verse-backend/app/api/a2a.py (registered tag-only, no prefix,
 *   included in app/bootstrap/routers.py). Endpoints exercised here:
 *   - GET  /.well-known/agent.json  (public agent card for A2A discovery)
 *   - GET  /a2a/tasks                (list recent A2A tasks for the tenant)
 *
 * Run:
 *   npx playwright test --config=playwright.real-e2e.config.ts e2e/real-e2e/a2a.real.spec.ts
 */

import { expect } from '@playwright/test';
import { test, FRONTEND_BASE } from './fixtures';

test.describe('A2A — real agent-to-agent protocol', () => {
  test('agent card API returns real discovery data', async ({ api }) => {
    const resp = await api.get('/.well-known/agent.json');
    expect(resp.status()).toBe(200);
    const body = await resp.json();
    expect(body.agent_id).toBeTruthy();
    expect(Array.isArray(body.capabilities)).toBe(true);
    expect(body.authentication).toHaveProperty('scheme');
  });

  test('a2a tasks list API returns an array for a fresh tenant', async ({ api }) => {
    const resp = await api.get('/a2a/tasks');
    expect(resp.status()).toBe(200);
    const body = await resp.json();
    expect(Array.isArray(body)).toBe(true);
  });

  test('a2a page renders without a critical error', async ({ authedPage }) => {
    await authedPage.goto(`${FRONTEND_BASE}/a2a`, { waitUntil: 'networkidle' });
    await expect(authedPage.locator('body')).toBeVisible();
    const text = (await authedPage.locator('body').textContent()) ?? '';
    expect(text.toLowerCase()).not.toContain('internal server error');
    expect(text.length).toBeGreaterThan(0);
  });
});
