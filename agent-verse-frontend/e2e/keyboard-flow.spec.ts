import { test, expect } from '@playwright/test';

test.describe('Keyboard Navigation', () => {
  test('modal closes on Escape key', async ({ page }) => {
    await page.goto('/agents');
    await page.waitForLoadState('networkidle');

    // Try to open a modal (agent create or similar)
    const createBtn = page.locator('button:has-text("Create"), button:has-text("New Agent")').first();
    if (await createBtn.isVisible()) {
      await createBtn.click();

      // Modal should be visible
      const modal = page.locator('[role="dialog"], .modal, [data-dialog]').first();
      if (await modal.isVisible()) {
        // Press Escape
        await page.keyboard.press('Escape');

        // Modal should be closed
        await expect(modal).not.toBeVisible({ timeout: 2000 });
      }
    }
  });

  test('goal submission form is keyboard-navigable', async ({ page }) => {
    await page.goto('/goals');
    await page.waitForLoadState('networkidle');

    // Tab through interactive elements
    const goalInput = page.locator('textarea[placeholder*="goal"], input[placeholder*="goal"]').first();
    if (await goalInput.isVisible()) {
      await goalInput.focus();
      await expect(goalInput).toBeFocused();

      // Tab to submit button
      await page.keyboard.press('Tab');
      const focused = page.locator(':focus');
      await expect(focused).toBeVisible();
    }
  });
});
