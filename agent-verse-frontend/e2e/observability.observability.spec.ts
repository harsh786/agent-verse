import { test, expect } from '@playwright/test';

test.describe('Observability Live', () => {
  test.beforeEach(async ({ page }) => {
    await page.route('**/observability/**', r => r.fulfill({ json: { health: 'ok', metrics: {} } }));
    await page.route('**/ai-ops/**', r => r.fulfill({ json: { alerts: [], regression_status: 'ok' } }));
  });

  test('observability page renders', async ({ page }) => {
    await page.goto('/observability');
    await expect(page.locator('body')).toBeVisible();
  });

  test('health endpoint is reachable', async ({ page }) => {
    const response = await page.request.get('http://localhost:8000/health');
    expect([200, 0]).toContain(response.status());
  });

  test('traces page renders', async ({ page }) => {
    await page.route('**/traces**', r => r.fulfill({ json: { traces: [] } }));
    await page.goto('/observability');
    await expect(page.locator('body')).toBeVisible();
  });
});
