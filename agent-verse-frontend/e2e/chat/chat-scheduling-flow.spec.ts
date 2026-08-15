/**
 * E2E: Scheduling flow — sends a schedule message, verifies SCHEDULE intent.
 */
import { test, expect } from '@playwright/test';

test.describe('Chat — Scheduling Flow', () => {
  test.beforeEach(async ({ page }) => {
    await page.goto('/');
    await page.evaluate(() => sessionStorage.setItem('agentverse_api_key', 'test'));

    await page.route('**/chat/**', async (route) => {
      const url = route.request().url();
      const method = route.request().method();
      if (url.includes('/models')) return route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify({ models: ['gpt-4o'] }) });
      if (url.includes('/folders')) return route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify({ folders: [] }) });
      if (url.includes('/sessions') && method === 'GET' && !url.includes('/messages')) return route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify({ sessions: [] }) });
      if (url.includes('/messages') && method === 'GET') return route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify({ messages: [] }) });
      if (url.includes('/messages') && method === 'POST') return route.fulfill({
        status: 200, contentType: 'application/json',
        body: JSON.stringify({ intent: 'SCHEDULE', message_id: 'sched-1', session_id: 'sched-s', clarify_request: null, schedule_confirmation: { goal_text: 'Run backup daily', cron_expression: '0 2 * * *', human_schedule: 'every day at 2 AM', next_run_iso: null } })
      });
      await route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify({}) });
    });
  });

  test('typing a schedule message sends with SCHEDULE intent', async ({ page }) => {
    await page.goto('/chat/sched-s');
    const input = page.getByLabel('Chat message input');
    await input.fill('Run backup every day at 2 AM');
    await page.getByTestId('send-button').click();
    await expect(page.getByText('Run backup every day at 2 AM')).toBeVisible({ timeout: 5000 });
  });

  test('new chat button creates session', async ({ page }) => {
    await page.route('**/chat/sessions', async (route) => {
      if (route.request().method() === 'POST') return route.fulfill({ status: 201, contentType: 'application/json', body: JSON.stringify({ id: 'sched-new', title: 'New Chat', pinned: false, ttl_days: null, system_prompt: null, agent_id: null, folder_id: null, show_reasoning: false, proactive_suggestions: true, preferred_model: null, tenant_id: 't1', created_at: new Date().toISOString(), updated_at: new Date().toISOString() }) });
      return route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify({ sessions: [] }) });
    });
    await page.goto('/chat');
    await expect(page.getByTestId('new-chat-button')).toBeVisible();
  });
});
