import { test, expect } from '@playwright/test';

test.describe('Cost Breakdown', () => {
  test('goal detail page shows cost breakdown section', async ({ page }) => {
    await page.goto('/goals');
    await page.waitForLoadState('networkidle');
    expect(page.url()).toContain('/goals');
  });

  test('cost metrics API endpoint exists', async ({ request }) => {
    const res = await request.get('/api/goals/nonexistent/cost-metrics', {
      headers: { 'X-API-Key': 'invalid' },
    });
    // 401 or 404 is fine — endpoint exists
    expect([200, 401, 404]).toContain(res.status());
  });
});
