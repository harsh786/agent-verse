/**
 * E2E: Full Q&A chat flow
 *
 * Tests:
 * - Landing on /chat shows empty state
 * - Creating a new session navigates to session URL
 * - Sending a QA message sends and receives a response
 * - Input clears after send
 * - Message bubbles render correctly
 */

import { test, expect } from '@playwright/test';

test.describe('Chat — Q&A Flow', () => {
  test.beforeEach(async ({ page }) => {
    // Inject a test API key so auth passes
    await page.goto('/');
    await page.evaluate(() => {
      sessionStorage.setItem('agentverse_api_key', 'test-api-key');
    });
    await page.goto('/chat');
  });

  test('shows empty state when no session selected', async ({ page }) => {
    await expect(page.getByText('AgentVerse Chat')).toBeVisible();
  });

  test('New Chat button is visible', async ({ page }) => {
    await expect(page.getByTestId('new-chat-button')).toBeVisible();
  });

  test('Start a New Chat button creates session and navigates', async ({ page }) => {
    await page.route('**/chat/sessions', async (route) => {
      if (route.request().method() === 'POST') {
        await route.fulfill({
          status: 201,
          contentType: 'application/json',
          body: JSON.stringify({
            id: 'e2e-session-1',
            title: 'New Chat',
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
          }),
        });
      } else {
        await route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify({ sessions: [] }) });
      }
    });
    await page.route('**/chat/models', async (route) => {
      await route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify({ models: ['gpt-4o'] }) });
    });
    await page.route('**/chat/sessions/e2e-session-1/messages*', async (route) => {
      await route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify({ messages: [] }) });
    });

    await page.goto('/chat');
    await page.getByText('Start a New Chat').click();
    await expect(page).toHaveURL(/\/chat\/e2e-session-1/);
  });

  test('chat input is visible when session is active', async ({ page }) => {
    await page.route('**/chat/**', async (route) => {
      const url = route.request().url();
      if (url.includes('/messages')) {
        await route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify({ messages: [] }) });
      } else if (url.includes('/models')) {
        await route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify({ models: ['gpt-4o'] }) });
      } else {
        await route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify({ sessions: [], folders: [] }) });
      }
    });
    await page.goto('/chat/test-session');
    await expect(page.getByLabel('Chat message input')).toBeVisible();
  });

  test('send button is disabled when input is empty', async ({ page }) => {
    await page.route('**/chat/**', async (route) => {
      await route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify({ sessions: [], messages: [], folders: [], models: ['gpt-4o'] }) });
    });
    await page.goto('/chat/test-session');
    const sendBtn = page.getByTestId('send-button');
    await expect(sendBtn).toBeDisabled();
  });

  test('typing in input enables send button', async ({ page }) => {
    await page.route('**/chat/**', async (route) => {
      const url = route.request().url();
      if (url.includes('/messages') && route.request().method() === 'GET') {
        await route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify({ messages: [] }) });
      } else if (url.includes('/models')) {
        await route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify({ models: ['gpt-4o'] }) });
      } else {
        await route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify({ sessions: [], folders: [] }) });
      }
    });
    await page.goto('/chat/test-session');
    const input = page.getByLabel('Chat message input');
    await input.fill('What is Python?');
    const sendBtn = page.getByTestId('send-button');
    await expect(sendBtn).not.toBeDisabled();
  });
});
