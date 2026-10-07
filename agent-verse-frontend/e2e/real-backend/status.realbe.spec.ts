/**
 * REAL-BACKEND e2e: the public status page against a genuinely running backend
 * (real :8000 API) — NOT mocked. a10-F244-02 / a10-F254-01: assert the live
 * GET /status payload (shape, no credentials needed, no error text leaked) and
 * that the page renders exactly what the backend reported. Read-only: no tenant
 * is minted. Runs under `--project=real-backend`.
 */
import { test, expect, request as pwRequest } from '@playwright/test';

const API_BASE = process.env.API_BASE_URL ?? 'http://localhost:8000';

const BANNER: Record<string, string> = {
  operational: 'All Systems Operational',
  degraded: 'Partial Service Disruption',
  unknown: 'Status Unknown',
};

test('real backend: /status is public and its payload drives the status page', async ({ page }) => {
  const ctx = await pwRequest.newContext();
  const resp = await ctx.get(`${API_BASE}/status`);
  expect(resp.status(), await resp.text()).toBe(200);
  const body = await resp.json();
  await ctx.dispose();

  expect(Object.keys(BANNER)).toContain(body.status);
  expect(typeof body.timestamp).toBe('number');
  expect(body.components?.api?.status).toBeDefined();
  for (const comp of Object.values(body.components as Record<string, Record<string, unknown>>)) {
    expect(['operational', 'degraded', 'unknown']).toContain(comp.status);
    expect(comp).not.toHaveProperty('error');
  }

  await page.goto('/status');
  await expect(page.getByText(BANNER[body.status])).toBeVisible({ timeout: 20_000 });
  const list = page.getByRole('list', { name: /service components/i });
  for (const name of Object.keys(body.components)) {
    await expect(list.getByRole('listitem').filter({ hasText: name.replace(/_/g, ' ') })).toHaveCount(1);
  }
});
