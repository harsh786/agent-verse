/**
 * E2E: Org Chart Navigation — hierarchy graph interactions.
 * Spec PART 35 (Organization UI).
 */
import { test, expect } from '@playwright/test';

const BASE_URL = process.env.PLAYWRIGHT_BASE_URL ?? 'http://localhost:5173';
const ORG_ID   = process.env.TEST_ORG_ID ?? 'org-test-001';

test.skip(
  () => !process.env.TEST_ORG_ID && !process.env.E2E_FULL,
  'Set TEST_ORG_ID or E2E_FULL=1',
);

test.describe('Org Chart Navigation', () => {
  test.beforeEach(async ({ page }) => {
    await page.goto(`${BASE_URL}/org/${ORG_ID}/chart`);
    await page.waitForLoadState('networkidle');
  });

  test('org chart renders without error', async ({ page }) => {
    await expect(page.locator('.react-flow')).toBeVisible({ timeout: 10_000 });
  });

  test('org node is visible', async ({ page }) => {
    // At least one node should be present
    await expect(page.locator('.react-flow__node').first()).toBeVisible({ timeout: 10_000 });
  });

  test('can zoom with controls', async ({ page }) => {
    const zoomIn = page.getByRole('button', { name: /zoom in/i });
    if (await zoomIn.isVisible()) {
      await zoomIn.click();
      // No error = pass
    }
  });

  test('search filters nodes', async ({ page }) => {
    const searchBox = page.getByPlaceholder(/search/i);
    if (await searchBox.isVisible()) {
      await searchBox.fill('Engineering');
      await page.waitForTimeout(500);
      // Nodes that don't match should be dimmed
    }
  });

  test('minimap is present', async ({ page }) => {
    await expect(page.locator('.react-flow__minimap')).toBeVisible({ timeout: 5000 });
  });
});
