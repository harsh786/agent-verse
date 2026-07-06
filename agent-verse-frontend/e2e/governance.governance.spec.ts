import { test, expect } from '@playwright/test';

test.describe('Governance Live', () => {
  test.beforeEach(async ({ page }) => {
    await page.route('**/approvals/**', r => r.fulfill({ json: { approvals: [] } }));
    await page.route('**/governance/**', r => r.fulfill({ json: { policies: [], audit: [] } }));
    await page.route('**/audit/**', r => r.fulfill({ json: { records: [] } }));
  });

  test('governance page renders', async ({ page }) => {
    await page.goto('/governance');
    await expect(page.locator('body')).toBeVisible();
  });

  test('audit log endpoint exists', async ({ page }) => {
    const response = await page.request.get('http://localhost:8000/governance/audit-log');
    expect(response.status()).not.toBe(404);
  });

  test('approvals endpoint exists', async ({ page }) => {
    const response = await page.request.get('http://localhost:8000/approvals');
    expect(response.status()).not.toBe(404);
  });
});
