/** E2E: Reasoning transparency */
import { test, expect } from '@playwright/test';
test.describe('Chat — Reasoning Transparency', () => {
  test.beforeEach(async ({ page }) => {
    await page.goto('/');
    await page.evaluate(() => sessionStorage.setItem('agentverse_api_key', 'test'));
    await page.route('**/chat/**', async (r) => {
      const u = r.request().url(); const m = r.request().method();
      if (u.includes('/models')) return r.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify({ models: ['gpt-4o'] }) });
      if (u.includes('/folders')) return r.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify({ folders: [] }) });
      if (u.includes('/sessions') && m === 'GET' && !u.includes('/messages')) return r.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify({ sessions: [{ id: 'r-s', title: 'Reasoning Test', pinned: false, show_reasoning: true, proactive_suggestions: true, preferred_model: null, system_prompt: null, agent_id: null, folder_id: null, ttl_days: null, tenant_id: 't1', created_at: new Date().toISOString(), updated_at: new Date().toISOString() }] }) });
      if (u.includes('/messages') && m === 'GET') return r.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify({ messages: [] }) });
      await r.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify({}) });
    });
  });
  test('session with show_reasoning appears in list', async ({ page }) => {
    await page.goto('/chat');
    await expect(page.getByText('Reasoning Test')).toBeVisible({ timeout: 5000 });
  });
  test('chat input visible in reasoning session', async ({ page }) => {
    await page.goto('/chat/r-s');
    await expect(page.getByLabel('Chat message input')).toBeVisible();
  });
});
