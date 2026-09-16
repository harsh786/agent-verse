/**
 * RPA Live — E2E Tests
 *
 * Covers /rpa/live (src/features/rpa/RpaLivePage.tsx) — the only route for the
 * rpa feature (there's no bare /rpa).
 *
 * Endpoints mocked:
 *   GET    /rpa/sessions
 *   POST   /rpa/sessions
 *   DELETE /rpa/sessions/{id}
 *   GET    /rpa/sessions/{id}/screenshot
 *   POST   /rpa/sessions/{id}/takeover
 *   GET    /rpa/tools
 *   POST   /rpa/execute
 */
import { test, expect, type Page } from '@playwright/test';
import { setupAuth } from './helpers/auth';

// ── Mock data ─────────────────────────────────────────────────────────────────

interface MockSession {
  session_id: string;
  status: 'active' | 'paused' | 'closed';
  created_at: string;
}

const SESSION_A: MockSession = {
  session_id: 'sess-aaaa1111bbbb2222',
  status: 'active',
  created_at: new Date(Date.now() - 60_000).toISOString(),
};

const SCREENSHOT_B64 =
  'iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNk+M9QDwADhgGAWjR9awAAAABJRU5ErkJggg==';

const TOOLS = [
  { name: 'rpa_click', description: 'Click at coordinates', risk: 'high' },
  { name: 'rpa_type', description: 'Type text into the focused element', risk: 'low' },
  { name: 'rpa_screenshot', description: 'Capture the current viewport', risk: 'read' },
];

async function mockRpaApi(
  page: Page,
  { sessions = [] as MockSession[] } = {}
): Promise<void> {
  let currentSessions = [...sessions];

  await page.route('**/rpa/tools', (route) =>
    route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify({ tools: TOOLS }) })
  );

  await page.route(/\/rpa\/sessions\/[^/]+\/screenshot/, (route) => {
    const id = route.request().url().match(/\/rpa\/sessions\/([^/]+)\/screenshot/)?.[1] ?? '';
    return route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({
        session_id: id,
        screenshot_data_uri: `data:image/png;base64,${SCREENSHOT_B64}`,
        url: 'https://example.com',
      }),
    });
  });

  await page.route(/\/rpa\/sessions\/[^/]+\/takeover/, (route) =>
    route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({ session_id: 'x', status: 'paused', message: 'Takeover requested.' }),
    })
  );

  await page.route(/\/rpa\/sessions\/[^/?]+$/, (route) => {
    if (route.request().method() === 'DELETE') {
      const id = route.request().url().split('/rpa/sessions/')[1];
      currentSessions = currentSessions.filter((s) => s.session_id !== id);
      return route.fulfill({ status: 204, body: '' });
    }
    return route.continue();
  });

  await page.route('**/rpa/sessions', (route) => {
    const method = route.request().method();
    if (method === 'POST') {
      const created: MockSession = {
        session_id: 'sess-new0000',
        status: 'active',
        created_at: new Date().toISOString(),
      };
      currentSessions = [...currentSessions, created];
      return route.fulfill({ status: 201, contentType: 'application/json', body: JSON.stringify(created) });
    }
    return route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(currentSessions) });
  });

  await page.route('**/rpa/execute', (route) => {
    const body = JSON.parse(route.request().postData() ?? '{}');
    return route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({
        success: true,
        output: `Executed ${body.tool_name}`,
        tool_name: body.tool_name,
        session_id: body.session_id,
        duration_ms: 42,
      }),
    });
  });
}

test.describe('RPA Live — empty state', () => {
  test('1. Shows "No active sessions" and lets you create one', async ({ page }) => {
    await setupAuth(page);
    await mockRpaApi(page, { sessions: [] });
    await page.goto('/rpa/live');

    await expect(page.getByRole('heading', { name: 'RPA Live' })).toBeAttached({ timeout: 10000 });
    await expect(page.getByText(/no active sessions/i)).toBeVisible();
    await expect(page.getByText(/no session selected/i)).toBeVisible();
  });
});

test.describe('RPA Live — populated state', () => {
  test('2. Session list renders, selecting a session shows the viewport', async ({ page }) => {
    await setupAuth(page);
    await mockRpaApi(page, { sessions: [SESSION_A] });
    await page.goto('/rpa/live');

    await expect(page.getByText(/^1$/).first()).toBeVisible({ timeout: 10000 });
    await expect(page.getByText(SESSION_A.session_id.slice(0, 14))).toBeVisible();

    await page.getByText(SESSION_A.session_id.slice(0, 14)).click();

    await expect(page.getByTestId('viewport-screenshot')).toBeVisible({ timeout: 10000 });
    await expect(page.getByText('active').first()).toBeVisible();
  });

  test('3. Clicking the viewport screenshot sends an rpa_click and logs the action', async ({ page }) => {
    await setupAuth(page);
    await mockRpaApi(page, { sessions: [SESSION_A] });
    await page.goto('/rpa/live');

    await page.getByText(SESSION_A.session_id.slice(0, 14)).click();
    const img = page.getByTestId('viewport-screenshot');
    await expect(img).toBeVisible({ timeout: 10000 });

    await img.click({ position: { x: 50, y: 50 } });

    await expect(page.getByText(/rpa_click/i)).toBeVisible({ timeout: 5000 });
  });
});

test.describe('RPA Live — primary interactions', () => {
  test('4. Create session button creates and selects a new session', async ({ page }) => {
    await setupAuth(page);
    await mockRpaApi(page, { sessions: [] });
    await page.goto('/rpa/live');

    await expect(page.getByText(/no active sessions/i)).toBeVisible({ timeout: 10000 });
    await page.getByRole('button', { name: /new session/i }).click();

    await expect(page.getByTestId('viewport-screenshot')).toBeVisible({ timeout: 10000 });
  });

  test('5. Tool console opens, selecting a tool and executing shows the result', async ({ page }) => {
    await setupAuth(page);
    await mockRpaApi(page, { sessions: [SESSION_A] });
    await page.goto('/rpa/live');

    await page.getByText(SESSION_A.session_id.slice(0, 14)).click();
    await expect(page.getByTestId('viewport-screenshot')).toBeVisible({ timeout: 10000 });

    await page.getByRole('button', { name: /tool console/i }).click();
    await expect(page.getByText('rpa_type')).toBeVisible({ timeout: 5000 });
    await page.getByText('rpa_type').click();

    await page.getByRole('button', { name: /^execute$/i }).click();
    await expect(page.getByText(/Executed rpa_type/i)).toBeVisible({ timeout: 5000 });
  });

  test('6. Takeover modal requests takeover with a reason', async ({ page }) => {
    await setupAuth(page);
    await mockRpaApi(page, { sessions: [SESSION_A] });
    await page.goto('/rpa/live');

    await page.getByText(SESSION_A.session_id.slice(0, 14)).click();
    await expect(page.getByTestId('viewport-screenshot')).toBeVisible({ timeout: 10000 });

    await page.getByRole('button', { name: /takeover/i }).click();
    await expect(page.getByRole('heading', { name: /take over browser session/i })).toBeVisible();
    await page.getByLabel(/takeover reason/i).fill('Manual login required');
    await page.getByRole('button', { name: /request takeover/i }).click();

    // Modal closes automatically on a successful takeover request.
    await expect(page.getByRole('heading', { name: /take over browser session/i })).not.toBeVisible({
      timeout: 5000,
    });
  });

  test('7. Closing a session removes it from the list', async ({ page }) => {
    await setupAuth(page);
    await mockRpaApi(page, { sessions: [SESSION_A] });
    await page.goto('/rpa/live');

    await expect(page.getByText(SESSION_A.session_id.slice(0, 14))).toBeVisible({ timeout: 10000 });

    // Opacity-based hover reveal doesn't block Playwright's actionability checks,
    // so the close button can be clicked directly without hovering first.
    await page.getByLabel('Close session').click();
    await page.getByRole('dialog').getByRole('button', { name: /close session/i }).click();

    await expect(page.getByText(/no active sessions/i)).toBeVisible({ timeout: 5000 });
  });
});
