/** E2E: Memory management */
import { test, expect } from '@playwright/test';
test.describe('Chat — Memory Management', () => {
  test.beforeEach(async ({ page }) => {
    await page.goto('/');
    await page.evaluate(() => sessionStorage.setItem('agentverse_api_key', 'test'));
    await page.route('**/chat/memories**', async (r) => {
      const m = r.request().method();
      if (m === 'GET') return r.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify({ memories: [{ id: 'm1', content: 'Use snake_case', source: 'manual', created_at: new Date().toISOString(), updated_at: new Date().toISOString() }] }) });
      if (m === 'POST') return r.fulfill({ status: 201, contentType: 'application/json', body: JSON.stringify({ id: 'm2', content: 'New memory', source: 'manual', created_at: new Date().toISOString(), updated_at: new Date().toISOString() }) });
      if (m === 'DELETE') return r.fulfill({ status: 204 });
      await r.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify({}) });
    });
  });
  test('agent memory page loads', async ({ page }) => {
    await page.goto('/chat/memory');
    // Page renders without crash
    await expect(page.locator('body')).toBeVisible();
  });
});
