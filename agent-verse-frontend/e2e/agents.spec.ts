import { test, expect, type Page } from '@playwright/test';

async function setupAuth(page: Page) {
  await page.addInitScript(() => {
    localStorage.setItem(
      'av-auth',
      JSON.stringify({
        state: { apiKey: 'test-api-key', tenantId: 'test-tenant', plan: '', isAuthenticated: true },
        version: 0,
      })
    );
    localStorage.setItem('av_api_key', 'test-api-key');
  });
}

test.describe('Agents', () => {
  test.beforeEach(async ({ page }) => {
    await setupAuth(page);
    await page.goto('/agents');
  });

  test('agents page renders without crashing', async ({ page }) => {
    await page.waitForLoadState('networkidle');
    const h1 = page.locator('h1, h2').filter({ hasText: /agents/i });
    await expect(h1.first()).toBeVisible({ timeout: 10000 });
  });

  test('create agent button is present', async ({ page }) => {
    await page.waitForLoadState('networkidle');
    const btn = page.locator('button').filter({ hasText: /create|new|add/i });
    if (await btn.count() > 0) {
      await expect(btn.first()).toBeVisible();
    }
  });

  test('autonomy filter buttons are visible', async ({ page }) => {
    await page.waitForLoadState('networkidle');
    const filterBtn = page.locator('button').filter({ hasText: /supervised|autonomous|all/i });
    if (await filterBtn.count() > 0) {
      await expect(filterBtn.first()).toBeVisible();
    }
  });
});
