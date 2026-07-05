import { test, expect } from '@playwright/test';

const HIGH_TRAFFIC_PAGES = [
  { name: 'goals-list', url: '/goals' },
  { name: 'agents-list', url: '/agents' },
  { name: 'marketplace', url: '/marketplace' },
  { name: 'status-page', url: '/status' },
];

// Snapshot tests are only meaningful in CI where baselines are committed.
// Run `npm run test:e2e:update-snapshots` to generate/update baselines.
const SNAPSHOT_ENABLED = process.env.CI === 'true' || process.env.UPDATE_SNAPSHOTS === 'true';

test.describe('Visual Regression', () => {
  test.beforeEach(async ({ page }) => {
    await page.setViewportSize({ width: 1280, height: 720 });
  });

  for (const { name, url } of HIGH_TRAFFIC_PAGES) {
    test(`${name} visual snapshot`, async ({ page }) => {
      await page.goto(url);
      await page.waitForLoadState('networkidle');
      await page.waitForTimeout(500);

      if (SNAPSHOT_ENABLED) {
        await expect(page).toHaveScreenshot(`${name}.png`, {
          maxDiffPixels: 200,
          animations: 'disabled',
          mask: [
            page.locator('[data-testid="live-cost-ticker"]'),
            page.locator('time'),
            page.locator('[aria-live]'),
          ],
        });
      } else {
        // In local dev without baselines: just verify the page loads without errors
        const title = await page.title();
        expect(title).toContain('AgentVerse');
        const h1 = page.locator('h1').first();
        // Page has some content
        await expect(page.locator('body')).not.toBeEmpty();
      }
    });
  }
});

test.describe('Accessibility', () => {
  for (const { name, url } of HIGH_TRAFFIC_PAGES.slice(0, 2)) {
    test(`${name} page loads without errors`, async ({ page }) => {
      const errors: string[] = [];
      page.on('pageerror', err => errors.push(err.message));

      await page.goto(url);
      await page.waitForLoadState('networkidle');

      // No critical JS errors
      const criticalErrors = errors.filter(e =>
        !e.includes('Failed to fetch') &&
        !e.includes('NetworkError') &&
        !e.includes('net::ERR')
      );
      expect(criticalErrors, `${name} has JS errors: ${criticalErrors.join(', ')}`).toHaveLength(0);

      // Images have alt text (critical a11y)
      const imgsWithoutAlt = await page.locator('img:not([alt])').count();
      expect(imgsWithoutAlt, `${name} has ${imgsWithoutAlt} images without alt text`).toBe(0);
    });
  }
});
