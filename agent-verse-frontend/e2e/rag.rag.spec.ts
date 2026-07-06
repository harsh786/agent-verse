import { test, expect } from '@playwright/test';

test.describe('RAG Live', () => {
  test.beforeEach(async ({ page }) => {
    // Pin to localhost:8000 so the mock never intercepts Vite's source-file requests
    await page.route('http://localhost:8000/rag-platform**', r => r.fulfill({ json: { answer: 'test answer', citations: [], strategy: 'direct' } }));
    await page.route('http://localhost:8000/knowledge**', r => r.fulfill({ json: { collections: [], results: [] } }));
  });

  test('knowledge search page renders', async ({ page }) => {
    await page.goto('/knowledge');
    await expect(page.locator('body')).toBeVisible();
  });

  test('RAG API query endpoint exists', async ({ page }) => {
    const response = await page.request.post('http://localhost:8000/rag-platform/query', {
      headers: { 'Content-Type': 'application/json' },
      data: { query: 'test query', strategy: 'auto' }
    });
    expect(response.status()).not.toBe(404);
  });
});
