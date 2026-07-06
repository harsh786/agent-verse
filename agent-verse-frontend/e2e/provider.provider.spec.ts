import { test, expect } from '@playwright/test';

const LIVE = !!process.env.LIVE_PROVIDER_TESTS;

test.describe('Provider Live Tests', () => {
  test.skip(!LIVE, 'Set LIVE_PROVIDER_TESTS=true to run provider tests');

  test('model registry lists available providers', async ({ page }) => {
    const response = await page.request.get('http://localhost:8000/model-registry/models', {
      headers: { 'X-API-Key': process.env.TEST_API_KEY ?? 'test-key' }
    });
    expect(response.ok()).toBe(true);
    const data = await response.json();
    expect(Array.isArray(data.models ?? data)).toBe(true);
  });

  test('goal submission returns goal_id', async ({ page }) => {
    const response = await page.request.post('http://localhost:8000/goals', {
      headers: { 'X-API-Key': process.env.TEST_API_KEY ?? 'test-key', 'Content-Type': 'application/json' },
      data: { goal: 'What is 2+2?', priority: 'normal', dry_run: true }
    });
    expect(response.ok()).toBe(true);
    const data = await response.json();
    expect(data.goal_id).toBeTruthy();
  });
});
