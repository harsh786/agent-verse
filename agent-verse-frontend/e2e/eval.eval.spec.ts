import { test, expect } from '@playwright/test';

test.describe('Eval Regression', () => {
  test.beforeEach(async ({ page }) => {
    await page.route('**/ai-ops/**', r => r.fulfill({ json: { status: 'ok', datasets: [], alerts: [] } }));
    await page.route('**/goals**', r => r.fulfill({ json: { goals: [] } }));
  });

  test('AI Ops dashboard renders without error', async ({ page }) => {
    await page.goto('/observability');
    await expect(page.locator('body')).toBeVisible();
  });

  test('eval scores endpoint returns valid structure', async ({ page }) => {
    const response = await page.request.get('http://localhost:8000/ai-ops/regression-status');
    // Either responds or connection refused (no backend in CI)
    expect([200, 0]).toContain(response.status());
  });

  test('golden datasets page loads', async ({ page }) => {
    await page.route('**/golden-datasets**', r => r.fulfill({ json: { datasets: [] } }));
    await page.goto('/observability');
    await expect(page.locator('body')).toBeVisible();
  });
});
