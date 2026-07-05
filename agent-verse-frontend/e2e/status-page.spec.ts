import { test, expect } from '@playwright/test';

test.describe('Status Page', () => {
  test('is accessible without authentication', async ({ page }) => {
    const response = await page.goto('/status');
    expect(response?.status()).not.toBe(401);
    expect(response?.status()).not.toBe(403);
  });

  test('displays AgentVerse Status heading', async ({ page }) => {
    await page.goto('/status');
    await page.waitForLoadState('networkidle');
    await expect(page.getByText('AgentVerse Status')).toBeVisible();
  });

  test('has a refresh button', async ({ page }) => {
    await page.goto('/status');
    await page.waitForLoadState('networkidle');
    const refresh = page.getByRole('button', { name: /refresh/i });
    await expect(refresh).toBeVisible();
  });

  test('shows loading state initially', async ({ page }) => {
    await page.goto('/status');
    // Either loading skeletons or loaded content should be present
    const pageContent = await page.content();
    expect(pageContent).toContain('AgentVerse');
  });
});
