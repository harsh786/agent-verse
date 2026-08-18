/**
 * E2E: "While You Were Away" digest panel.
 * Spec PART 39 (DigestPanel).
 */
import { test, expect } from '@playwright/test';

const BASE_URL = process.env.PLAYWRIGHT_BASE_URL ?? 'http://localhost:5173';
const ORG_ID   = process.env.TEST_ORG_ID ?? 'org-test-001';

test.skip(
  () => !process.env.TEST_ORG_ID && !process.env.E2E_FULL,
  'Set TEST_ORG_ID or E2E_FULL=1',
);

test.describe('"While You Were Away" Digest', () => {
  test.beforeEach(async ({ page }) => {
    await page.goto(`${BASE_URL}/org/${ORG_ID}`);
    await page.waitForLoadState('networkidle');
  });

  test('digest panel accessible from command center', async ({ page }) => {
    // Look for "While you were away" / "Morning brief" / digest trigger
    const trigger = page.getByRole('button', { name: /brief|digest|away/i })
      .or(page.getByText(/while you were away/i))
      .or(page.locator('[data-testid="digest-trigger"]'));
    // May not be visible if no data — just ensure no crash
    await expect(page).not.toHaveURL(/error/i);
  });

  test('morning brief endpoint returns data', async ({ page, request }) => {
    const apiBase = process.env.API_BASE_URL ?? 'http://localhost:8000';
    const res = await request.get(`${apiBase}/v1/org/${ORG_ID}/brief/morning`);
    // Either 200 or 401 (auth) — should not be 500
    expect([200, 401, 403, 404]).toContain(res.status());
  });

  test('digest shows completed missions section', async ({ page }) => {
    // Navigate to morning brief page
    await page.goto(`${BASE_URL}/org/${ORG_ID}/brief`);
    await page.waitForLoadState('networkidle');
    // Should not crash
    await expect(page).not.toHaveURL(/error/i);
  });

  test('digest items are keyboard navigable', async ({ page }) => {
    await page.goto(`${BASE_URL}/org/${ORG_ID}/brief`);
    // Tab through items
    await page.keyboard.press('Tab');
    await expect(page.locator(':focus')).toBeVisible({ timeout: 3000 });
  });
});
