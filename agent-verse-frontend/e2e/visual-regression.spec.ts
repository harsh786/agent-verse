import { test, expect } from '@playwright/test';

const HIGH_TRAFFIC_PAGES = [
  { name: 'goals-list', url: '/goals' },
  { name: 'agents-list', url: '/agents' },
  { name: 'marketplace', url: '/marketplace' },
];

test.describe('Visual Regression', () => {
  test.beforeEach(async ({ page }) => {
    // Set consistent viewport
    await page.setViewportSize({ width: 1280, height: 720 });
  });

  for (const { name, url } of HIGH_TRAFFIC_PAGES) {
    test(`${name} visual snapshot`, async ({ page }) => {
      await page.goto(url);
      await page.waitForLoadState('networkidle');

      // Wait for any loading states to resolve
      await page.waitForTimeout(500);

      await expect(page).toHaveScreenshot(`${name}.png`, {
        maxDiffPixels: 100,
        animations: 'disabled',
      });
    });
  }
});
