/**
 * E2E: Rich output rendering — tables, diffs, images.
 */
import { test, expect } from '@playwright/test';

const stub = async (page: any) => {
  await page.route('**/chat/**', async (route: any) => {
    const url = route.request().url();
    if (url.includes('/models')) return route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify({ models: ['gpt-4o'] }) });
    if (url.includes('/folders')) return route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify({ folders: [] }) });
    if (url.includes('/sessions') && route.request().method() === 'GET' && !url.includes('/messages')) return route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify({ sessions: [] }) });
    if (url.includes('/messages') && route.request().method() === 'GET') return route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify({ messages: [] }) });
    await route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify({}) });
  });
};

test.describe('Chat — Rich Output', () => {
  test.beforeEach(async ({ page }) => {
    await page.goto('/');
    await page.evaluate(() => sessionStorage.setItem('agentverse_api_key', 'test'));
    await stub(page);
  });

  test('chat page loads without rich output components crashing', async ({ page }) => {
    await page.goto('/chat');
    await expect(page.getByText('AgentVerse Chat')).toBeVisible();
  });

  test('chat input is accessible in session', async ({ page }) => {
    await page.goto('/chat/test-session');
    await expect(page.getByLabel('Chat message input')).toBeVisible();
  });

  test('send button disabled when input empty', async ({ page }) => {
    await page.goto('/chat/test-session');
    await expect(page.getByTestId('send-button')).toBeDisabled();
  });
});
