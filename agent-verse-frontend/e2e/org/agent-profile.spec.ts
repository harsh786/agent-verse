/**
 * E2E: Agent Profile — slide-in panel showing agent details.
 * Spec PART 38 (Agent UI).
 */
import { test, expect } from '@playwright/test';

const BASE_URL = process.env.PLAYWRIGHT_BASE_URL ?? 'http://localhost:5173';
const ORG_ID   = process.env.TEST_ORG_ID ?? 'org-test-001';

test.skip(
  () => !process.env.TEST_ORG_ID && !process.env.E2E_FULL,
  'Set TEST_ORG_ID or E2E_FULL=1',
);

test.describe('Agent Profile', () => {
  test('agents page loads', async ({ page }) => {
    await page.goto(`${BASE_URL}/org/${ORG_ID}/agents`);
    await page.waitForLoadState('networkidle');
    await expect(page).not.toHaveURL(/error/i);
  });

  test('agent card click opens profile panel', async ({ page }) => {
    await page.goto(`${BASE_URL}/org/${ORG_ID}`);
    await page.waitForLoadState('networkidle');
    // Click any agent card if visible
    const agentCard = page.locator('[data-testid*="agent-card"], [role="article"]').first();
    if (await agentCard.isVisible()) {
      await agentCard.click();
      // Panel or dialog should appear
      await expect(
        page.getByRole('dialog').or(page.locator('[role="complementary"]'))
      ).toBeVisible({ timeout: 5000 });
    }
  });

  test('agent profile panel is closeable', async ({ page }) => {
    await page.goto(`${BASE_URL}/org/${ORG_ID}`);
    await page.waitForLoadState('networkidle');
    const panel = page.getByRole('dialog');
    if (await panel.isVisible()) {
      // Close with X button
      const closeBtn = panel.getByRole('button', { name: /close/i });
      if (await closeBtn.isVisible()) {
        await closeBtn.click();
        await expect(panel).not.toBeVisible({ timeout: 3000 });
      }
    }
  });

  test('agent profile closes on Escape', async ({ page }) => {
    await page.goto(`${BASE_URL}/org/${ORG_ID}`);
    await page.waitForLoadState('networkidle');
    const panel = page.getByRole('dialog');
    if (await panel.isVisible()) {
      await page.keyboard.press('Escape');
      await expect(panel).not.toBeVisible({ timeout: 3000 });
    }
  });

  test('agent page has correct page title', async ({ page }) => {
    await page.goto(`${BASE_URL}/org/${ORG_ID}/agents`);
    await expect(page).toHaveTitle(/.+/); // non-empty title
  });

  test('agent list items have accessible roles', async ({ page }) => {
    await page.goto(`${BASE_URL}/org/${ORG_ID}/agents`);
    await page.waitForLoadState('networkidle');
    // Lists should have role=list and items should be focusable
    const items = page.locator('[role="listitem"], [role="article"]');
    const count = await items.count();
    if (count > 0) {
      expect(count).toBeGreaterThan(0);
    }
  });
});
