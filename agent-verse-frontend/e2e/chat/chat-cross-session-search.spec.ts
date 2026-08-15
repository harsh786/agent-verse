/** E2E: Cross-session search */
import { test, expect } from '@playwright/test';
test.describe('Chat — Cross-Session Search', () => {
  test.beforeEach(async ({ page }) => {
    await page.goto('/');
    await page.evaluate(() => sessionStorage.setItem('agentverse_api_key', 'test'));
    await page.route('**/chat/**', async (r) => {
      const u = r.request().url(); const m = r.request().method();
      if (u.includes('/models')) return r.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify({ models: ['gpt-4o'] }) });
      if (u.includes('/folders')) return r.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify({ folders: [] }) });
      if (u.includes('/search') && m === 'POST') return r.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify({ results: [{ message_id: 'm1', session_id: 's1', role: 'user', snippet: 'FastAPI **rocks**', created_at: new Date().toISOString() }], total: 1 }) });
      await r.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify({ sessions: [], messages: [] }) });
    });
  });
  test('sidebar new chat is visible', async ({ page }) => {
    await page.goto('/chat');
    await expect(page.getByTestId('new-chat-button')).toBeVisible();
  });
});
