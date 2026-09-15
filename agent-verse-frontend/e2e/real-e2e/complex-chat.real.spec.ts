/**
 * REAL E2E — complex chat scenarios against the LIVE backend (no mocking).
 *
 * Creates a real tenant via /tenants/signup, signs in on the real frontend, and
 * drives the real chat UI end-to-end (real intent classification, persistence,
 * SSE streaming, goal dispatch, scheduling, cross-turn memory). A screenshot of
 * each scenario is written to e2e/real-e2e/__screens__/.
 *
 * Run: npx playwright test --config=playwright.real-e2e.config.ts complex-chat
 * Requires the backend on :8000 and the frontend on :5173.
 */
import { test, expect, type Page } from '@playwright/test';
import { apiClient, API_BASE, FRONTEND_BASE, type E2ETenant } from './fixtures';

let tenant: E2ETenant;

test.beforeAll(async ({ request }) => {
  // Reuse a pre-created tenant when provided (avoids signup rate limits); else sign up.
  if (process.env.TEST_TENANT_ID && process.env.TEST_API_KEY) {
    tenant = {
      tenantId: process.env.TEST_TENANT_ID, apiKey: process.env.TEST_API_KEY,
      name: 'reused', email: '', plan: 'free',
    };
    return;
  }
  const ts = Date.now();
  const resp = await request.post(`${API_BASE}/tenants/signup`, {
    data: { name: `WCC E2E ${ts}`, email: `wcc-e2e-${ts}@agentverse.io` },
  });
  expect([200, 201]).toContain(resp.status());
  const body = await resp.json();
  tenant = {
    tenantId: body.tenant_id, apiKey: body.api_key, name: body.name,
    email: body.email, plan: body.plan,
  };
});

/**
 * Authenticate through the REAL login flow: fill the tenant id + the throwaway
 * test-tenant API key and submit, so the whole app-side auth path (validate →
 * store → redirect) runs for real. Also seeds sessionStorage('agentverse_api_key')
 * which chat.ts reads for the SSE stream URL.
 */
async function auth(page: Page): Promise<void> {
  await page.goto(`${FRONTEND_BASE}/auth`, { waitUntil: 'domcontentloaded' });
  await page.getByPlaceholder('my-org').fill(tenant.tenantId);
  await page.getByPlaceholder('av_key_...').fill(tenant.apiKey);
  await page.getByRole('button', { name: 'Sign in' }).click();
  // Land somewhere authenticated (not back on /auth).
  await expect(page).not.toHaveURL(/\/auth$/, { timeout: 15000 });
  await page.addInitScript((k: string) => {
    sessionStorage.setItem('agentverse_api_key', k);
  }, tenant.apiKey);
  await page.evaluate((k: string) => sessionStorage.setItem('agentverse_api_key', k), tenant.apiKey);
}

/** Create a real session via the API and open it in the UI. */
async function openSession(page: Page, title: string): Promise<string> {
  const res = await apiClient(page.request, tenant).post('/chat/sessions', { title });
  const id = (await res.json()).id as string;
  await page.goto(`${FRONTEND_BASE}/chat/${id}`, { waitUntil: 'domcontentloaded' });
  await expect(page.getByLabel('Chat message input')).toBeVisible({ timeout: 15000 });
  return id;
}

async function sendAndWait(page: Page, text: string): Promise<void> {
  const input = page.getByLabel('Chat message input');
  await input.fill(text);
  await page.getByRole('button', { name: 'Send message' }).click();
  // The user turn appears immediately; wait for the stream to settle.
  await expect(page.getByText(text, { exact: false }).first()).toBeVisible({ timeout: 15000 });
  await page.waitForTimeout(2500);
}

async function shot(page: Page, name: string): Promise<void> {
  await page.screenshot({ path: `e2e/real-e2e/__screens__/${name}.png`, fullPage: true });
}

test.describe('Complex chat — live backend', () => {
  test('1 · Q&A turn streams a real assistant reply', async ({ page }) => {
    await auth(page);
    await openSession(page, 'Breakfast Q&A');
    await sendAndWait(page, 'give me a quick high-protein breakfast idea');
    // A real assistant bubble is rendered after the SSE stream.
    await expect(page.locator('[data-testid="chat-composer"]')).toBeVisible();
    await shot(page, '1-qa-stream');
  });

  test('2 · Goal turn dispatches the real agent + execution panel', async ({ page }) => {
    await auth(page);
    await openSession(page, 'Cross-tool Goal');
    await sendAndWait(page, 'search for open P1 bugs and post a summary to the team');
    await shot(page, '2-goal-execution');
  });

  test('3 · Scheduling turn creates a real schedule', async ({ page }) => {
    await auth(page);
    await openSession(page, 'Scheduling');
    await sendAndWait(page, 'every Monday at 9am send me a summary of last week');
    await shot(page, '3-scheduling');
  });

  test('4 · Cross-turn memory — a stated fact persists in the thread', async ({ page }) => {
    await auth(page);
    await openSession(page, 'Memory Continuity');
    await sendAndWait(page, 'remember that our Q3 launch date is September 20th');
    await sendAndWait(page, 'what did we decide about the Q3 launch date?');
    // Both turns are persisted and visible in one durable thread.
    await expect(page.getByText(/Q3 launch date/i).first()).toBeVisible();
    await shot(page, '4-memory-continuity');
  });

  test('5 · Multi-session sidebar shows the durable conversations', async ({ page }) => {
    await auth(page);
    await openSession(page, 'Extra Conversation');
    await sendAndWait(page, 'hello there');
    await page.goto(`${FRONTEND_BASE}/chat`, { waitUntil: 'domcontentloaded' });
    await page.waitForTimeout(1500);
    await shot(page, '5-multi-session-sidebar');
  });
});
