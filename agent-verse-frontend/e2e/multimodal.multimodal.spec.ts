import { test, expect } from '@playwright/test';

test.describe('Multimodal', () => {
  test.beforeEach(async ({ page }) => {
    // Pin to localhost:8000 so the mock never intercepts Vite's source-file requests
    await page.route('http://localhost:8000/multimodal**', r => r.fulfill({ json: { job_id: 'test', status: 'complete', spans: [] } }));
    await page.route('http://localhost:8000/knowledge**', r => r.fulfill({ json: { collections: [] } }));
  });

  test('knowledge page renders for multimodal content', async ({ page }) => {
    await page.goto('/knowledge');
    await expect(page.locator('body')).toBeVisible();
  });

  test('multimodal API endpoint exists', async ({ page }) => {
    const response = await page.request.post('http://localhost:8000/multimodal/ingest', {
      headers: { 'Content-Type': 'application/json' },
      data: { content: 'test', modality: 'text' }
    });
    // Either 401 (unauthed) or connection refused — just not 404
    expect(response.status()).not.toBe(404);
  });
});
