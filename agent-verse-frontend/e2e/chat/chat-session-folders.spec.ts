/** E2E: Session folders */
import { test, expect } from '@playwright/test';
test.describe('Chat — Session Folders', () => {
  test.beforeEach(async ({ page }) => {
    await page.goto('/');
    await page.evaluate(() => sessionStorage.setItem('agentverse_api_key', 'test'));
    await page.route('**/chat/**', async (r) => {
      const u = r.request().url(); const m = r.request().method();
      if (u.includes('/models')) return r.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify({ models: ['gpt-4o'] }) });
      if (u.includes('/folders') && m === 'GET') return r.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify({ folders: [{ id: 'f1', name: 'Work', color: '#6366f1' }] }) });
      if (u.includes('/folders') && m === 'POST') return r.fulfill({ status: 201, contentType: 'application/json', body: JSON.stringify({ id: 'f2', name: 'New Folder', color: '#6366f1', tenant_id: 't1' }) });
      await r.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify({ sessions: [], messages: [] }) });
    });
  });
  test('sidebar shows folder section when folders exist', async ({ page }) => {
    await page.goto('/chat');
    await expect(page.getByText('Work')).toBeVisible({ timeout: 5000 });
  });
});
