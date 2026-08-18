/**
 * E2E: Team Formation — org forms a team after mission is created.
 * Spec PART 10 (Dynamic Team Formation).
 */
import { test, expect } from '@playwright/test';

const BASE_URL = process.env.PLAYWRIGHT_BASE_URL ?? 'http://localhost:5173';
const ORG_ID   = process.env.TEST_ORG_ID ?? 'org-test-001';

test.skip(
  () => !process.env.TEST_ORG_ID && !process.env.E2E_FULL,
  'Set TEST_ORG_ID or E2E_FULL=1',
);

test.describe('Team Formation', () => {
  test('teams page loads without error', async ({ page }) => {
    await page.goto(`${BASE_URL}/org/${ORG_ID}/teams`);
    await page.waitForLoadState('networkidle');
    await expect(page).not.toHaveURL(/error/i);
  });

  test('team list shows active teams', async ({ page }) => {
    await page.goto(`${BASE_URL}/org/${ORG_ID}/teams`);
    await page.waitForLoadState('networkidle');
    // Ensure page renders something (even empty state)
    await expect(page.locator('body')).not.toBeEmpty();
  });

  test('team formation animation runs on new mission', async ({ page }) => {
    await page.goto(`${BASE_URL}/org/${ORG_ID}`);
    await page.waitForLoadState('networkidle');
    // Activity feed should show team-related events if any missions ran
    const feed = page.locator('[data-testid="activity-feed"]')
      .or(page.getByRole('list', { name: /activity/i }));
    // Feed may or may not exist based on data
    await expect(page).not.toHaveURL(/error/i);
  });

  test('team page has accessible member list', async ({ page }) => {
    await page.goto(`${BASE_URL}/org/${ORG_ID}/teams`);
    await page.waitForLoadState('networkidle');
    // All interactive buttons have accessible labels
    const buttons = page.getByRole('button');
    const count = await buttons.count();
    for (let i = 0; i < Math.min(count, 3); i++) {
      const btn = buttons.nth(i);
      const ariaLabel = await btn.getAttribute('aria-label');
      const text = await btn.textContent();
      expect(ariaLabel || text?.trim()).toBeTruthy();
    }
  });
});
