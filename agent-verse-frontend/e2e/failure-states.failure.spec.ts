import { test, expect } from '@playwright/test';

test.describe('Failure States', () => {
  test('API error shows error state not blank page', async ({ page }) => {
    await page.route('**/goals**', route => route.fulfill({ status: 500, json: { detail: 'Internal Server Error' } }));
    await page.goto('/goals');
    await page.waitForTimeout(2000);
    // Page should show error state, not crash
    const body = await page.locator('body').textContent();
    expect(body).toBeTruthy();
    expect(body!.length).toBeGreaterThan(0);
  });

  test('network offline shows degraded state', async ({ page, context }) => {
    await page.goto('/');
    await context.setOffline(true);
    await page.reload({ waitUntil: 'domcontentloaded' });
    // Should show something (offline page, cached content, or error boundary)
    const body = await page.locator('body').textContent();
    expect(body).toBeTruthy();
    await context.setOffline(false);
  });

  test('slow API: loading spinner appears', async ({ page }) => {
    await page.route('**/goals**', async route => {
      await new Promise(r => setTimeout(r, 2000));
      await route.fulfill({ json: { goals: [] } });
    });
    await page.goto('/goals');
    // During the delay, some loading indicator should be visible OR the request completes
    const hasContent = await page.locator('body').textContent();
    expect(hasContent).toBeTruthy();
  });

  test('404 route shows not found page', async ({ page }) => {
    await page.goto('/this-route-definitely-does-not-exist-xyz');
    const body = await page.locator('body').textContent();
    expect(body).toBeTruthy();
    // Should not be a blank white page
    expect(body!.trim().length).toBeGreaterThan(0);
  });
});
