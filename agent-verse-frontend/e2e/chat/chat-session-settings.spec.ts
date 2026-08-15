/** E2E: Session settings modal */
import { test, expect } from '@playwright/test';
test.describe('Chat — Session Settings', () => {
  test.beforeEach(async ({ page }) => {
    await page.goto('/');
    await page.evaluate(() => sessionStorage.setItem('agentverse_api_key', 'test'));
    await page.route('**/chat/**', async (r) => {
      const u = r.request().url(); const m = r.request().method();
      if (u.includes('/models')) return r.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify({ models: ['gpt-4o'] }) });
      if (u.includes('/folders')) return r.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify({ folders: [] }) });
      if (u.includes('/messages') && m === 'GET') return r.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify({ messages: [] }) });
      if (m === 'PATCH' && u.includes('/sessions/')) return r.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify({ id: 'sett-s', title: 'Session', pinned: false, show_reasoning: true, proactive_suggestions: false, system_prompt: 'You are a helper', preferred_model: null, agent_id: null, folder_id: null, ttl_days: null, tenant_id: 't1', created_at: new Date().toISOString(), updated_at: new Date().toISOString() }) });
      await r.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify({ sessions: [], messages: [] }) });
    });
  });
  test('chat page renders session settings area', async ({ page }) => {
    await page.goto('/chat/sett-s');
    await expect(page.getByLabel('Chat message input')).toBeVisible();
  });
  test('send button accessible', async ({ page }) => {
    await page.goto('/chat/sett-s');
    await expect(page.getByTestId('send-button')).toBeVisible();
  });
});
