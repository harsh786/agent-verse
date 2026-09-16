/**
 * Real E2E tests for the Channel Mappings feature — NO HTTP mocking.
 *
 * Route: /channel-mappings → src/features/channels/ChannelMappingsPage.tsx
 * Backend: agent-verse-backend/app/api/channels/ingestion.py (prefix "/channels").
 *   - GET  /channels/mappings  (list channel-to-tenant mappings)
 *   - POST /channels/mappings  (register a channel, e.g. a Slack workspace)
 *
 * Run:
 *   npx playwright test --config=playwright.real-e2e.config.ts e2e/real-e2e/channels.real.spec.ts
 */

import { expect } from '@playwright/test';
import { test, FRONTEND_BASE } from './fixtures';

test.describe('Channels — real channel mappings', () => {
  test('channel mappings list API returns an array for a fresh tenant', async ({ api }) => {
    const resp = await api.get('/channels/mappings');
    expect(resp.status()).toBe(200);
    const body = await resp.json();
    expect(Array.isArray(body)).toBe(true);
  });

  test('create channel mapping via real API', async ({ api }) => {
    const resp = await api.post('/channels/mappings', {
      channel_type: 'slack',
      channel_id: `T-e2e-${Date.now()}`,
    });
    expect([200, 201]).toContain(resp.status());
    const body = await resp.json();
    expect(body.status).toBeTruthy();
  });

  test('channel mappings page renders without a critical error', async ({ authedPage }) => {
    await authedPage.goto(`${FRONTEND_BASE}/channel-mappings`, { waitUntil: 'networkidle' });
    await expect(authedPage.locator('body')).toBeVisible();
    const text = (await authedPage.locator('body').textContent()) ?? '';
    expect(text.toLowerCase()).not.toContain('internal server error');
    expect(text.length).toBeGreaterThan(0);
  });
});
