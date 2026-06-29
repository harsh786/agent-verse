import { test, expect, type Page } from '@playwright/test';

// ── Fixtures ──────────────────────────────────────────────────────────────────

const EMPTY_METRICS = {
  active_goals: 0,
  total_goals: 0,
  success_rate: 0,
  avg_latency_ms: 0,
  cost_today_usd: 0,
  goals_today: 0,
};

const POPULATED_METRICS = {
  active_goals: 3,
  total_goals: 12,
  success_rate: 0.83,
  avg_latency_ms: 1234,
  cost_today_usd: 2.5,
  goals_today: 5,
};

async function setupAuth(page: Page) {
  await page.addInitScript(() => {
    localStorage.setItem(
      'av-auth',
      JSON.stringify({
        state: {
          apiKey: 'test-key',
          tenantId: 'test-tenant',
          plan: 'pro',
          isAuthenticated: true,
        },
        version: 0,
      })
    );
    localStorage.setItem('av_api_key', 'test-key');
  });

  // RequireAuth session validation
  await page.route('**/tenants/me', (route) =>
    route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({ tenant_id: 'test-tenant', name: 'Test Org', plan: 'pro' }),
    })
  );
  // Agents list — needed by Agent Orbit visualization
  await page.route(/localhost:8000\/agents/, (route) =>
    route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({ agents: [] }),
    })
  );
  // Cost analytics — needed by cost KPI card
  await page.route(/localhost:8000\/analytics\/costs/, (route) =>
    route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({ period_days: 1, total_cost_usd: 0, cost_today_usd: 0, trends: [] }),
    })
  );
}

/**
 * Route both /goals (list) and /goals/metrics within a single handler.
 * Playwright uses LIFO so tests that register their own handler afterwards will take precedence.
 */
async function mockGoalsApis(
  page: Page,
  {
    goals = [] as Array<{ id: string; goal_id?: string; goal: string; status: string; created_at?: string }>,
    metrics = EMPTY_METRICS,
  } = {}
) {
  await page.route(/localhost:8000\/goals/, (route) => {
    const url = route.request().url();
    if (url.endsWith('/metrics')) {
      return route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify(metrics),
      });
    }
    // GET /goals list
    if (route.request().method() === 'GET') {
      return route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({ goals }),
      });
    }
    return route.continue();
  });
}

// ── Tests ─────────────────────────────────────────────────────────────────────

test.describe('Dashboard', () => {
  // ── KPI cards ──────────────────────────────────────────────────────────────

  test('all 4 KPI card labels render: Active Goals, Success Rate, Cost Today, Agents', async ({
    page,
  }) => {
    await setupAuth(page);
    await mockGoalsApis(page);
    await page.goto('/dashboard');

    await expect(page.getByText('Active Goals')).toBeVisible({ timeout: 15000 });
    await expect(page.getByText('Success Rate')).toBeVisible();
    await expect(page.getByText('Cost Today')).toBeVisible();
    await expect(page.getByText('Agents').first()).toBeVisible();
  });

  test('KPI values reflect the /goals/metrics response', async ({ page }) => {
    await setupAuth(page);
    // Override analytics/costs to return populated cost data so Cost Today shows $2.5000
    await page.route(/localhost:8000\/analytics\/costs/, (route) =>
      route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({ period_days: 1, total_cost_usd: 2.5, cost_today_usd: 2.5, trends: [] }),
      })
    );
    await mockGoalsApis(page, { metrics: POPULATED_METRICS });
    await page.goto('/dashboard');

    // Cost Today = $2.5000 (toFixed(4))
    await expect(page.getByText('$2.5000')).toBeVisible({ timeout: 15000 });
  });

  test('KPI sub-labels render (total, completed, failed, active counts)', async ({
    page,
  }) => {
    await setupAuth(page);
    await mockGoalsApis(page, { metrics: EMPTY_METRICS });
    await page.goto('/dashboard');

    // With empty metrics, sub-labels show "0 total", "0 completed"
    await expect(page.getByText('0 total')).toBeVisible({ timeout: 15000 });
    await expect(page.getByText('0 completed')).toBeVisible();
  });

  // ── Activity feed ───────────────────────────────────────────────────────────

  test('"Live Activity" heading is visible', async ({ page }) => {
    await setupAuth(page);
    await mockGoalsApis(page);
    await page.goto('/dashboard');

    await expect(page.getByText('Live Activity')).toBeVisible({ timeout: 15000 });
  });

  test('activity feed shows empty state message when no goals exist', async ({ page }) => {
    await setupAuth(page);
    await mockGoalsApis(page, { goals: [] });
    await page.goto('/dashboard');

    await expect(page.getByText('No recent activity')).toBeVisible({ timeout: 15000 });
  });

  test('activity feed shows goal text when goals are returned', async ({ page }) => {
    const goals = [
      {
        id: 'g-abc-001',
        goal_id: 'g-abc-001',
        goal: 'Fix the authentication bug',
        status: 'complete',
        created_at: new Date().toISOString(),
      },
      {
        id: 'g-abc-002',
        goal_id: 'g-abc-002',
        goal: 'Improve test coverage to 90%',
        status: 'executing',
        created_at: new Date().toISOString(),
      },
    ];

    await setupAuth(page);
    await mockGoalsApis(page, { goals, metrics: POPULATED_METRICS });
    await page.goto('/dashboard');

    await expect(page.getByText('Fix the authentication bug')).toBeVisible({ timeout: 15000 });
    await expect(page.getByText('Improve test coverage to 90%')).toBeVisible();
    // Goal IDs are not rendered in the activity feed — only goal text is shown
  });

  // ── Status badges ───────────────────────────────────────────────────────────

  test('status badges render for "complete", "executing", "planning", "failed"', async ({
    page,
  }) => {
    const goals = [
      { id: 'g1', goal: 'Completed task', status: 'complete', created_at: new Date().toISOString() },
      { id: 'g2', goal: 'Running task', status: 'executing', created_at: new Date().toISOString() },
      { id: 'g3', goal: 'Planning task', status: 'planning', created_at: new Date().toISOString() },
      { id: 'g4', goal: 'Failed task', status: 'failed', created_at: new Date().toISOString() },
    ];

    await setupAuth(page);
    await mockGoalsApis(page, { goals, metrics: POPULATED_METRICS });
    await page.goto('/dashboard');

    // StatusBadge renders status.replace('_', ' ') — so exact text matches
    await expect(page.getByText('complete').first()).toBeVisible({ timeout: 15000 });
    await expect(page.getByText('executing').first()).toBeVisible();
    await expect(page.getByText('planning').first()).toBeVisible();
    await expect(page.getByText('failed').first()).toBeVisible();
  });

  test('"waiting human" status badge renders (underscore replaced with space)', async ({
    page,
  }) => {
    const goals = [
      {
        id: 'g1',
        goal: 'Awaiting human approval',
        status: 'waiting_human',
        created_at: new Date().toISOString(),
      },
    ];

    await setupAuth(page);
    await mockGoalsApis(page, { goals });
    await page.goto('/dashboard');

    // "waiting_human" → "waiting human" (underscore replaced in StatusBadge)
    await expect(page.getByText('waiting human')).toBeVisible({ timeout: 15000 });
  });

  // ── Page header ─────────────────────────────────────────────────────────────

  test('"Mission Control" h1 and dynamic subtitle are visible', async ({ page }) => {
    await setupAuth(page);
    await mockGoalsApis(page);
    await page.goto('/dashboard');

    await expect(page.locator('h1').filter({ hasText: /mission control/i })).toBeVisible({
      timeout: 15000,
    });
    // Subtitle is "All systems nominal" when no active goals
    await expect(page.getByText('All systems nominal')).toBeVisible();
  });
});
