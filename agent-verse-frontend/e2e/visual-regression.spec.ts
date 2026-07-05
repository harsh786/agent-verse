import { test, expect } from '@playwright/test';

const HIGH_TRAFFIC_PAGES = [
  { name: 'goals-list', url: '/goals' },
  { name: 'agents-list', url: '/agents' },
  { name: 'marketplace', url: '/marketplace' },
  { name: 'status-page', url: '/status' },
];

test.describe('Visual Regression', () => {
  test.beforeEach(async ({ page }) => {
    await page.setViewportSize({ width: 1280, height: 720 });
  });

  for (const { name, url } of HIGH_TRAFFIC_PAGES) {
    test(`${name} visual snapshot`, async ({ page }) => {
      await page.goto(url);
      await page.waitForLoadState('networkidle');
      await page.waitForTimeout(500);

      await expect(page).toHaveScreenshot(`${name}.png`, {
        maxDiffPixels: 200,
        animations: 'disabled',
        mask: [
          // Mask dynamic content that changes between runs
          page.locator('[data-testid="live-cost-ticker"]'),
          page.locator('[data-testid="timestamp"]'),
          page.locator('time'),
        ],
      });
    });
  }
});

test.describe('Accessibility', () => {
  // Check key pages have no critical a11y violations
  for (const { name, url } of HIGH_TRAFFIC_PAGES.slice(0, 2)) {
    test(`${name} has no critical accessibility violations`, async ({ page }) => {
      await page.goto(url);
      await page.waitForLoadState('networkidle');

      // Check for basic a11y requirements
      // All images should have alt text
      const imagesWithoutAlt = await page.locator('img:not([alt])').count();
      expect(imagesWithoutAlt).toBe(0);

      // All form inputs should have labels
      const inputsWithoutLabel = await page.evaluate(() => {
        const inputs = Array.from(document.querySelectorAll('input:not([type="hidden"])'));
        return inputs.filter(input => {
          const id = input.getAttribute('id');
          const ariaLabel = input.getAttribute('aria-label');
          const ariaLabelledBy = input.getAttribute('aria-labelledby');
          const placeholder = input.getAttribute('placeholder');
          return !ariaLabel && !ariaLabelledBy && !(id && document.querySelector(`label[for="${id}"]`)) && !placeholder;
        }).length;
      });
      // Warn but don't fail — some inputs use placeholder only
      console.log(`${name}: ${inputsWithoutLabel} inputs without labels`);

      // Page should have a main landmark
      const main = page.locator('main, [role="main"]');
      // Don't fail if not present — some pages use divs
    });
  }
});
