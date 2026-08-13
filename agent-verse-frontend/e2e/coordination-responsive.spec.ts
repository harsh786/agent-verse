import { expect, test, type Page } from '@playwright/test';
import { setupAuth } from './helpers/auth';

async function mockEmptyRun(page: Page) {
  const prefix = '**/api/v1/coordination/sessions/session-1';
  await page.route(prefix, (route) => route.fulfill({ json: { session_id: 'session-1', tenant_id: 'test-tenant', state: 'completed', next_sequence: 1, version: 1 } }));
  for (const suffix of ['/messages**', '/moa/layers**', '/camel', '/generative']) await page.route(`${prefix}${suffix}`, (route) => route.fulfill({ json: { items: [] } }));
  await page.route(`${prefix}/ledger`, (route) => route.fulfill({ status: 404, json: { detail: 'missing' } }));
  await page.route(`${prefix}/swarm`, (route) => route.fulfill({ json: { nodes: [], edges: [] } }));
  await page.route(`${prefix}/auction`, (route) => route.fulfill({ json: { items: [], sealed_bid_count: 0 } }));
  await page.route(`${prefix}/events`, (route) => route.fulfill({ contentType: 'text/event-stream', body: ': heartbeat\n\n' }));
}

for (const viewport of [{ width: 375, height: 667 }, { width: 768, height: 1024 }, { width: 1280, height: 720 }, { width: 1440, height: 900 }]) {
  test(`coordination layout does not overflow at ${viewport.width}x${viewport.height}`, async ({ page }) => {
    await page.setViewportSize(viewport);
    await setupAuth(page);
    await mockEmptyRun(page);
    await page.goto('/coordination/session-1');
    await expect(page.getByRole('heading', { name: 'Coordination ledger' })).toBeVisible();
    const overflow = await page.evaluate(() => document.documentElement.scrollWidth > document.documentElement.clientWidth);
    expect(overflow).toBeFalsy();
  });
}

test('coordination remains usable at 200 percent zoom', async ({ page }) => {
  await setupAuth(page);
  await mockEmptyRun(page);
  await page.goto('/coordination/session-1');
  await page.evaluate(() => { document.documentElement.style.zoom = '2'; });
  await expect(page.getByLabel('Session ID')).toBeVisible();
});
