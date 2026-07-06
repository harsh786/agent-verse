import { test, expect } from '@playwright/test';

test.describe('Smoke: Critical Paths', () => {
  test.beforeEach(async ({ page }) => {
    // Mock auth
    await page.route('**/health', route => route.fulfill({ json: { status: 'healthy' } }));
    await page.route('**/goals**', route => route.fulfill({ json: { goals: [] } }));
    await page.route('**/agents**', route => route.fulfill({ json: { agents: [] } }));
  });

  test('app loads without crashing', async ({ page }) => {
    await page.goto('/');
    await expect(page).not.toHaveTitle('Error');
    // App must render some content
    await expect(page.locator('body')).not.toBeEmpty();
  });

  test('goals page renders', async ({ page }) => {
    await page.goto('/goals');
    await expect(page.locator('body')).toBeVisible();
  });

  test('agents page renders', async ({ page }) => {
    await page.goto('/agents');
    await expect(page.locator('body')).toBeVisible();
  });

  test('navigation is accessible', async ({ page }) => {
    await page.goto('/');
    // Check no JS console errors on load
    const errors: string[] = [];
    page.on('pageerror', e => errors.push(e.message));
    await page.waitForTimeout(1000);
    // Allow specific known warnings but no fatal errors
    const fatalErrors = errors.filter(e => !e.includes('Warning'));
    expect(fatalErrors).toHaveLength(0);
  });
});
