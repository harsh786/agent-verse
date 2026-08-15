/** E2E: Model selector */
import { test, expect } from '@playwright/test';
test.describe('Chat — Model Selector', () => {
  test.beforeEach(async ({ page }) => {
    await page.goto('/');
    await page.evaluate(() => sessionStorage.setItem('agentverse_api_key', 'test'));
    await page.route('**/chat/**', async (r) => {
      const u = r.request().url(); const m = r.request().method();
      if (u.includes('/models')) return r.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify({ models: ['gpt-4o', 'claude-3-5-sonnet', 'gpt-4o-mini'] }) });
      if (u.includes('/folders')) return r.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify({ folders: [] }) });
      if (u.includes('/messages') && m === 'GET') return r.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify({ messages: [] }) });
      await r.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify({ sessions: [], messages: [] }) });
    });
  });
  test('model selector renders in chat input area', async ({ page }) => {
    await page.goto('/chat/model-s');
    // Wait for models to load
    await page.waitForTimeout(500);
    const modelSelector = page.getByLabel('Select model');
    // Model selector should be present in the input area
    await expect(modelSelector).toBeVisible({ timeout: 5000 });
  });
});
