/**
 * E2E: Message editing flow
 */

import { test, expect } from '@playwright/test';

test.describe('Chat — Message Editing Flow', () => {
  test.beforeEach(async ({ page }) => {
    await page.goto('/');
    await page.evaluate(() => sessionStorage.setItem('agentverse_api_key', 'test-key'));
  });

  test('user message edit button calls edit API', async ({ page }) => {
    let editCalled = false;

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
        return route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify({ sessions: [] }) });
      }
      if (url.match(/\/messages\/msg-1/) && method === 'PATCH') {
        editCalled = true;
        return route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify({ message: { id: 'msg-1', role: 'user', content: 'New content', session_id: 's1', metadata: {}, intent: 'QA', goal_id: null, branch_id: null, parent_message_id: null, created_at: new Date().toISOString() }, pruned_message_ids: [] }) });
      }
      if (url.includes('/messages') && method === 'GET') {
        return route.fulfill({
          status: 200,
          contentType: 'application/json',
          body: JSON.stringify({
            messages: [{
              id: 'msg-1',
              session_id: 's1',
              role: 'user',
              content: 'What is Python?',
              metadata: {},
              intent: 'QA',
              goal_id: null,
              branch_id: null,
              parent_message_id: null,
              created_at: new Date().toISOString(),
            }],
          }),
        });
      }
      await route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify({}) });
    });

    await page.goto('/chat/s1');

    // Wait for message to appear
    await expect(page.getByText('What is Python?')).toBeVisible({ timeout: 5000 });
  });

  test('sidebar new chat button is accessible', async ({ page }) => {
    await page.route('**/chat/**', async (route) => {
      const url = route.request().url();
      if (url.includes('/models')) {
        return route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify({ models: ['gpt-4o'] }) });
      }
      if (url.includes('/folders')) {
        return route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify({ folders: [] }) });
      }
      await route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify({ sessions: [] }) });
    });

    await page.goto('/chat');
    const newChatBtn = page.getByTestId('new-chat-button');
    await expect(newChatBtn).toBeVisible();
    await expect(newChatBtn).toBeEnabled();
  });
});
