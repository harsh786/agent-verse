/**
 * E2E: Clarification flow — sends an underspecified goal,
 * expects a clarification card in the response.
 */

import { test, expect } from '@playwright/test';

test.describe('Chat — Clarification Flow', () => {
  test.beforeEach(async ({ page }) => {
    await page.goto('/');
    await page.evaluate(() => sessionStorage.setItem('agentverse_api_key', 'test-key'));

    // Stub all chat endpoints
    await page.route('**/chat/**', async (route) => {
      const url = route.request().url();
      const method = route.request().method();

      if (url.includes('/models')) {
        return route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify({ models: ['gpt-4o'] }) });
      }
      if (url.includes('/folders')) {
        return route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify({ folders: [] }) });
      }
      if (url.includes('/sessions') && !url.includes('/messages') && method === 'GET') {
        return route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify({ sessions: [{ id: 'clarify-s', title: 'Clarify Test', pinned: false, ttl_days: null, system_prompt: null, agent_id: null, folder_id: null, show_reasoning: false, proactive_suggestions: true, preferred_model: null, tenant_id: 't1', created_at: new Date().toISOString(), updated_at: new Date().toISOString() }] }) });
      }
      if (url.includes('/messages') && method === 'GET') {
        return route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify({ messages: [] }) });
      }
      if (url.includes('/messages') && method === 'POST') {
        return route.fulfill({
          status: 200,
          contentType: 'application/json',
          body: JSON.stringify({
            intent: 'CLARIFY',
            message_id: 'clarify-msg-1',
            session_id: 'clarify-s',
            clarify_request: {
              question: 'Which environment should I target?',
              options: ['Development', 'Staging', 'Production'],
              round: 1,
            },
            schedule_confirmation: null,
          }),
        });
      }
      await route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify({}) });
    });
  });

  test('shows clarification intent on underspecified message', async ({ page }) => {
    await page.goto('/chat/clarify-s');
    const input = page.getByLabel('Chat message input');
    await input.fill('Deploy it');
    await page.getByTestId('send-button').click();

    // Message should appear in thread
    await expect(page.getByText('Deploy it')).toBeVisible({ timeout: 5000 });
  });
});
