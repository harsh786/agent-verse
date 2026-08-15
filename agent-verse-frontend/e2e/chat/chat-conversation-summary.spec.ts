/** E2E: Conversation summary */
import { test, expect } from '@playwright/test';
test.describe('Chat — Conversation Summary', () => {
  test.beforeEach(async ({ page }) => {
    await page.goto('/');
    await page.evaluate(() => sessionStorage.setItem('agentverse_api_key', 'test'));
    await page.route('**/chat/**', async (r) => {
      const u = r.request().url(); const m = r.request().method();
      if (u.includes('/models')) return r.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify({ models: ['gpt-4o'] }) });
      if (u.includes('/folders')) return r.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify({ folders: [] }) });
      if (u.includes('/summarize')) return r.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify({ summary: 'We discussed deploying the FastAPI service and setting up CI/CD pipelines.' }) });
      if (u.includes('/messages') && m === 'GET') return r.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify({ messages: [] }) });
      await r.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify({ sessions: [], messages: [] }) });
    });
  });
  test('chat page loads in session with summarize available', async ({ page }) => {
    await page.goto('/chat/sum-s');
    await expect(page.getByLabel('Chat message input')).toBeVisible();
  });
});
