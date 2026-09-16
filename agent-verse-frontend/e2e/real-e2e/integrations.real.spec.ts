/**
 * Real E2E tests for Integrations — NO HTTP mocking.
 *
 * Route: /integrations, page src/features/integrations/IntegrationsPage.tsx.
 * Backend: agent-verse-backend/app/api/integrations.py (prefix /integrations).
 *
 * The integrations router is deliberately exempt from tenant auth middleware
 * (webhooks use their own auth — Slack signing secret, Zapier secret, etc.),
 * so the "real API" test hits it with no auth headers at all. The page itself
 * renders a static provider catalogue plus a live query against the Zapier
 * completed-goals poll endpoint.
 */
import { expect } from '@playwright/test';
import { test, API_BASE, FRONTEND_BASE } from './fixtures';

test.describe('Integrations — real backend', () => {
  test('GET /integrations/zapier/goals returns an array with no auth required', async ({ request }) => {
    const resp = await request.get(`${API_BASE}/integrations/zapier/goals`);
    expect(resp.status()).toBe(200);
    const body = await resp.json();
    expect(Array.isArray(body)).toBe(true);
  });

  test('integrations page renders the provider catalogue', async ({ authedPage }) => {
    await authedPage.goto(`${FRONTEND_BASE}/integrations`, { waitUntil: 'networkidle' });

    await expect(authedPage.getByRole('heading', { name: 'Integrations' })).toBeVisible({ timeout: 10000 });
    await expect(authedPage.getByRole('heading', { name: 'Slack', exact: true })).toBeVisible();
    await expect(authedPage.getByRole('heading', { name: 'Zapier', exact: true })).toBeVisible();
    await expect(authedPage.getByRole('heading', { name: 'Alertmanager' })).toBeVisible();
    await expect(authedPage.getByRole('heading', { name: 'Datadog' })).toBeVisible();

    // Endpoint paths are rendered verbatim for operators to copy.
    await expect(authedPage.getByText('/integrations/slack/commands')).toBeVisible();
  });

  test('integrations page shows the Zapier completed-goals panel with real (empty) data', async ({ authedPage }) => {
    await authedPage.goto(`${FRONTEND_BASE}/integrations`, { waitUntil: 'networkidle' });

    await expect(authedPage.getByRole('heading', { name: /Zapier — recent completed goals/i })).toBeVisible({ timeout: 10000 });
    // Fresh tenant has no completed goals visible to the (unconfigured) Zapier poll trigger.
    await expect(authedPage.getByText('No completed goals available to the Zapier poll trigger.')).toBeVisible();
  });

  test('copy-endpoint button is present for each provider endpoint', async ({ authedPage }) => {
    await authedPage.goto(`${FRONTEND_BASE}/integrations`, { waitUntil: 'networkidle' });

    await expect(authedPage.getByRole('heading', { name: 'Integrations' })).toBeVisible({ timeout: 10000 });
    await expect(authedPage.getByRole('button', { name: 'Copy endpoint Slash command' })).toBeVisible();
  });
});
