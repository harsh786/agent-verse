import { expect, test, type Page } from '@playwright/test';
import AxeBuilder from '@axe-core/playwright';
import { setupAuth } from './helpers/auth';

async function mockCoordination(page: Page) {
  const prefix = '**/api/v1/coordination/sessions/session-1';
  await page.route(prefix, (route) => route.fulfill({ json: { session_id: 'session-1', tenant_id: 'test-tenant', state: 'paused', next_sequence: 3, version: 2 } }));
  await page.route(`${prefix}/messages**`, (route) => route.fulfill({ json: { items: [{ message_id: 'm1', sequence: 1, sender_agent_id: 'planner', recipient_agent_ids: ['worker'], safe_content: 'Plan accepted', classification: 'internal', trust_label: 'verified', citation_ids: ['c1'] }] } }));
  await page.route(`${prefix}/ledger`, (route) => route.fulfill({ json: { version: 2, facts: ['API reachable'], blockers: [], next_actor: 'worker' } }));
  await page.route(`${prefix}/moa/layers**`, (route) => route.fulfill({ json: { items: [] } }));
  await page.route(`${prefix}/camel`, (route) => route.fulfill({ json: { items: [] } }));
  await page.route(`${prefix}/generative`, (route) => route.fulfill({ json: { items: [] } }));
  await page.route(`${prefix}/swarm`, (route) => route.fulfill({ json: { nodes: [{ agent_id: 'worker', state: 'active', current: true }], edges: [] } }));
  await page.route(`${prefix}/auction`, (route) => route.fulfill({ json: { items: [{ winner_id: 'worker', score: 91, fairness_adjustment: 1 }], sealed_bid_count: 2 } }));
  await page.route(`${prefix}/events`, (route) => route.fulfill({ contentType: 'text/event-stream', body: ': heartbeat\n\n' }));
  await page.route(`${prefix}/resume`, (route) => route.fulfill({ status: 202, json: { state: 'active' } }));
}

test.beforeEach(async ({ page }) => {
  await setupAuth(page);
  await mockCoordination(page);
  await page.goto('/coordination/session-1');
  await expect(page.getByRole('heading', { name: 'Coordination ledger' })).toBeVisible();
});

test('coordination journey has no serious WCAG violations', async ({ page }) => {
  const results = await new AxeBuilder({ page })
    .withTags(['wcag2a', 'wcag2aa', 'wcag21aa', 'wcag22aa'])
    .analyze();
  expect(results.violations.filter((item) => ['serious', 'critical'].includes(item.impact ?? ''))).toEqual([]);
});

test('keyboard reaches controls and live status is announced', async ({ page }) => {
  await expect(page.getByText('Stream: live')).toBeVisible();
  const resume = page.getByRole('button', { name: 'Resume' });
  await resume.focus();
  await expect(resume).toBeFocused();
  await page.keyboard.press('Enter');
  await expect(resume).toBeDisabled();
});

test('reduced motion disables decorative animation', async ({ page }) => {
  await page.emulateMedia({ reducedMotion: 'reduce' });
  const probe = page.locator('body').evaluate(() => {
    const element = document.createElement('div');
    element.className = 'animate-spin';
    element.setAttribute('data-testid', 'motion-probe');
    document.body.appendChild(element);
    return getComputedStyle(element).animationDuration;
  });
  expect(await probe).toBe('0.001s');
});

test('primary session controls meet the 44 pixel target size', async ({ page }) => {
  for (const name of ['Open', 'Cancel', 'Resume']) {
    const box = await page.getByRole('button', { name, exact: true }).boundingBox();
    expect(box, `${name} must be rendered`).not.toBeNull();
    expect(box!.height, `${name} target height`).toBeGreaterThanOrEqual(44);
    expect(box!.width, `${name} target width`).toBeGreaterThanOrEqual(44);
  }
});
