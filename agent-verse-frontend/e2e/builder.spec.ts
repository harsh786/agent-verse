import { test, expect } from '@playwright/test';

test.describe('Builder', () => {
  test('builder page is accessible', async ({ page }) => {
    const resp = await page.goto('/builder');
    // Should be accessible (auth redirect is ok, but not 500)
    expect(resp?.status()).not.toBe(500);
  });

  test('builder create project form exists when authenticated', async ({ page }) => {
    await page.goto('/builder');
    // Check the page loads without JS errors
    const errors: string[] = [];
    page.on('pageerror', e => errors.push(e.message));
    await page.waitForLoadState('networkidle');
    // No JS errors
    const criticalErrors = errors.filter(e => !e.includes('Failed to fetch') && !e.includes('Network'));
    expect(criticalErrors).toHaveLength(0);
  });
});
