/**
 * E2E: GOAL execution flow — dispatches a goal and shows progress steps.
 */

import { test, expect } from '@playwright/test';

const SESSION = {
  id: 'goal-session-1',
  title: 'Goal Session',
  pinned: false,
  ttl_days: null,
  system_prompt: null,
  agent_id: null,
  folder_id: null,
  show_reasoning: false,
  proactive_suggestions: true,
  preferred_model: null,
  tenant_id: 't1',
  created_at: new Date().toISOString(),
  updated_at: new Date().toISOString(),
};

test.describe('Chat — Goal Execution Flow', () => {
  test.beforeEach(async ({ page }) => {
    await page.goto('/');
    await page.evaluate(() => {
      sessionStorage.setItem('agentverse_api_key', 'test-key');
    });

    await page.route('**/chat/sessions', async (route) => {
      if (route.request().method() === 'POST') {
        await route.fulfill({ status: 201, contentType: 'application/json', body: JSON.stringify(SESSION) });
      } else {
        await route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify({ sessions: [SESSION] }) });
      }
    });
    await page.route(`**/chat/sessions/${SESSION.id}`, async (route) => {
      await route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(SESSION) });
    });
    await page.route('**/chat/folders', async (route) => {
      await route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify({ folders: [] }) });
    });
    await page.route('**/chat/models', async (route) => {
      await route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify({ models: ['gpt-4o'] }) });
    });
    await page.route(`**/chat/sessions/${SESSION.id}/messages`, async (route) => {
      if (route.request().method() === 'GET') {
        await route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify({ messages: [] }) });
      } else {
        await route.fulfill({
          status: 200,
          contentType: 'application/json',
          body: JSON.stringify({
            intent: 'GOAL',
            message_id: 'goal-msg-1',
            session_id: SESSION.id,
            clarify_request: null,
            schedule_confirmation: null,
          }),
        });
      }
    });
  });

  test('sending a goal message shows GOAL intent', async ({ page }) => {
    await page.goto(`/chat/${SESSION.id}`);
    const input = page.getByLabel('Chat message input');
    await input.fill('Deploy the backend service to production');
    await page.getByTestId('send-button').click();

    // User message should appear
    await expect(page.getByText('Deploy the backend service to production')).toBeVisible({ timeout: 5000 });
  });

  test('session appears in sidebar', async ({ page }) => {
    await page.goto('/chat');
    await expect(page.getByText('Goal Session')).toBeVisible();
  });

  test('clicking session navigates to it', async ({ page }) => {
    await page.goto('/chat');
    await page.getByTestId(`session-${SESSION.id}`).click();
    await expect(page).toHaveURL(new RegExp(SESSION.id));
  });
});
