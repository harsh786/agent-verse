import { test, expect, type Page, type Route } from '@playwright/test';

/**
 * Public status page. a10-F244-02 / a10-F254-01: the spec used to pass on any
 * response — it never looked at the backend `/status` payload. The page's
 * backend call is now intercepted with the payload shape the real endpoint
 * returns (pinned server-side in
 * agent-verse-backend/tests/api/test_uninventoried_pages_payload_contract.py),
 * and the rendered banner / components are asserted against it. The live
 * payload itself is checked by e2e/real-backend/status.realbe.spec.ts.
 */

type StatusPayload = {
  status: string;
  components: Record<string, { status: string; latency_ms?: number }>;
  timestamp: number;
  page_title?: string;
};

/** Intercept only the page's backend fetch of `/status` — not the SPA route. */
async function mockStatusApi(
  page: Page,
  reply: { status?: number; body: unknown },
): Promise<{ calls: Array<Record<string, string>> }> {
  const calls: Array<Record<string, string>> = [];
  await page.route(
    (url) => url.pathname === '/status',
    async (route: Route) => {
      const req = route.request();
      if (req.resourceType() !== 'fetch' && req.resourceType() !== 'xhr') {
        await route.fallback();
        return;
      }
      calls.push(req.headers());
      await route.fulfill({
        status: reply.status ?? 200,
        contentType: 'application/json',
        body: JSON.stringify(reply.body),
      });
    },
  );
  return { calls };
}

const DEGRADED: StatusPayload = {
  status: 'degraded',
  components: {
    api: { status: 'operational' },
    postgres: { status: 'degraded' },
    redis: { status: 'operational' },
  },
  timestamp: Date.now() / 1000,
  page_title: 'AgentVerse System Status',
};

test.describe('Status Page', () => {
  test('is accessible without authentication', async ({ page }) => {
    await mockStatusApi(page, { body: DEGRADED });
    const response = await page.goto('/status');
    expect(response?.status()).not.toBe(401);
    expect(response?.status()).not.toBe(403);
  });

  test('displays AgentVerse Status heading and a refresh button', async ({ page }) => {
    await mockStatusApi(page, { body: DEGRADED });
    await page.goto('/status');
    await expect(page.getByText('AgentVerse Status')).toBeVisible();
    await expect(page.getByRole('button', { name: /refresh/i })).toBeVisible();
  });

  test('renders the backend payload: degraded banner and every component', async ({ page }) => {
    const { calls } = await mockStatusApi(page, { body: DEGRADED });
    await page.goto('/status');

    await expect(page.getByText('Partial Service Disruption')).toBeVisible();
    await expect(page.getByText('Some services are experiencing issues.')).toBeVisible();
    const list = page.getByRole('list', { name: /service components/i });
    for (const [name, comp] of Object.entries(DEGRADED.components)) {
      const row = list.getByRole('listitem').filter({ hasText: name });
      await expect(row).toHaveCount(1);
      await expect(row).toContainText(comp.status);
    }
    // The public endpoint is called without credentials.
    expect(calls.length).toBeGreaterThan(0);
    expect(Object.keys(calls[0]).map((h) => h.toLowerCase())).not.toContain('x-api-key');
  });

  test('renders an operational payload as all systems operational', async ({ page }) => {
    await mockStatusApi(page, {
      body: { ...DEGRADED, status: 'operational', components: { api: { status: 'operational' } } },
    });
    await page.goto('/status');
    await expect(page.getByText('All Systems Operational')).toBeVisible();
    await expect(page.getByText('Partial Service Disruption')).toHaveCount(0);
  });

  test('an unknown overall status is shown as unknown, never operational', async ({ page }) => {
    await mockStatusApi(page, { body: { ...DEGRADED, status: 'unknown', components: {} } });
    await page.goto('/status');
    await expect(page.getByText('Status Unknown')).toBeVisible();
    await expect(page.getByText('All Systems Operational')).toHaveCount(0);
  });

  test('a failing status endpoint shows the error, not a status', async ({ page }) => {
    await mockStatusApi(page, { status: 404, body: { detail: 'Not Found' } });
    await page.goto('/status');
    await expect(page.getByRole('alert')).toContainText(/Unable to load status/i, {
      timeout: 15_000,
    });
    await expect(page.getByText('All Systems Operational')).toHaveCount(0);
  });
});
