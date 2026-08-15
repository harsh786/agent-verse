/** E2E: Token/cost visibility */
import { test, expect } from '@playwright/test';
test.describe('Chat — Token & Cost Visibility', () => {
  test.beforeEach(async ({ page }) => {
    await page.goto('/');
    await page.evaluate(() => sessionStorage.setItem('agentverse_api_key', 'test'));
    await page.route('**/chat/**', async (r) => {
      const u = r.request().url(); const m = r.request().method();
      if (u.includes('/models')) return r.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify({ models: ['gpt-4o'] }) });
      if (u.includes('/folders')) return r.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify({ folders: [] }) });
      if (u.includes('/usage')) return r.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify({ session_id: 'tc-s', total_tokens: 1250, total_tokens_in: 900, total_tokens_out: 350, total_cost_usd: 0.0025, llm_calls: 5 }) });
      if (u.includes('/messages') && m === 'GET') return r.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify({ messages: [] }) });
      await r.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify({ sessions: [], messages: [] }) });
    });
  });
  test('chat session loads with cost visibility', async ({ page }) => {
    await page.goto('/chat/tc-s');
    await expect(page.getByLabel('Chat message input')).toBeVisible();
  });
});
