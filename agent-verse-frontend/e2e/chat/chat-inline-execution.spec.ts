/** E2E: Inline code execution */
import { test, expect } from '@playwright/test';
test.describe('Chat — Inline Code Execution', () => {
  test.beforeEach(async ({ page }) => {
    await page.goto('/');
    await page.evaluate(() => sessionStorage.setItem('agentverse_api_key', 'test'));
    await page.route('**/chat/**', async (r) => {
      const u = r.request().url(); const m = r.request().method();
      if (u.includes('/models')) return r.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify({ models: ['gpt-4o'] }) });
      if (u.includes('/folders')) return r.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify({ folders: [] }) });
      if (u.includes('/execute') && m === 'POST') return r.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify({ exit_code: 0, stdout: 'hello world', stderr: '', language: 'python', duration_ms: 50, truncated: false, error: null }) });
      await r.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify({ sessions: [], messages: [] }) });
    });
  });
  test('chat loads in session', async ({ page }) => {
    await page.goto('/chat/exec-session');
    await expect(page.getByLabel('Chat message input')).toBeVisible();
  });
});
