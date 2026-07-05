import { test, expect } from '@playwright/test';

test.describe('Goal Feedback and Explain', () => {
  test.beforeEach(async ({ page }) => {
    await page.goto('/');
    // Skip auth for now — just check static rendering
  });

  test('status page is accessible without login', async ({ page }) => {
    await page.goto('/status');
    await expect(page).toHaveTitle(/AgentVerse/);
    const heading = page.getByText('AgentVerse Status');
    await expect(heading).toBeVisible();
  });

  test('status page has refresh button', async ({ page }) => {
    await page.goto('/status');
    const refresh = page.getByRole('button', { name: /refresh/i });
    await expect(refresh).toBeVisible();
  });
});
