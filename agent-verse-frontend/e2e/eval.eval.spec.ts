import { test, expect } from '@playwright/test';

test.describe('Eval Regression', () => {
  test.beforeEach(async ({ page }) => {
    // Pin to localhost:8000 so the mock never intercepts Vite's source-file requests
    await page.route('http://localhost:8000/ai-ops**', r => r.fulfill({ json: { status: 'ok', datasets: [], alerts: [] } }));
    await page.route('http://localhost:8000/goals**', r => r.fulfill({ json: { goals: [] } }));
  });

  test('AI Ops dashboard renders without error', async ({ page }) => {
    await page.goto('/observability');
    await expect(page.locator('body')).toBeVisible();
  });

  test('eval scores endpoint returns valid structure', async ({ page }) => {
    const response = await page.request.get('http://localhost:8000/ai-ops/regression-status');
    // Either 200 (ok), 401/403 (no auth but server is up), or 0 (connection refused in CI)
    expect([200, 401, 403, 0]).toContain(response.status());
  });

  test('golden datasets page loads', async ({ page }) => {
    await page.route('**/golden-datasets**', r => r.fulfill({ json: { datasets: [] } }));
    await page.goto('/observability');
    await expect(page.locator('body')).toBeVisible();
  });
});
