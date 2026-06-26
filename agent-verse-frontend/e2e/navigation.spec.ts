import { test, expect, type Page } from '@playwright/test';

// ── Shared auth + mock helpers ────────────────────────────────────────────────

async function setupAuth(page: Page) {
  // Inject Zustand persist state and raw API key into localStorage before page load
  await page.addInitScript(() => {
    localStorage.setItem(
      'av-auth',
      JSON.stringify({
        state: {
          apiKey: 'test-api-key',
          tenantId: 'test-tenant',
          plan: 'free',
          isAuthenticated: true,
        },
        version: 0,
      })
    );
    localStorage.setItem('av_api_key', 'test-api-key');
  });
}

/** Mock every backend endpoint that any page might call so tests run without a server. */
async function mockAllApis(page: Page) {
  // Session validation (RequireAuth calls this on every protected page mount)
  await page.route('**/tenants/me', (route) =>
    route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({ tenant_id: 'test-tenant', name: 'Test Org', plan: 'free' }),
    })
  );

  // Goals list + metrics
  await page.route(/localhost:8000\/goals/, (route) => {
    const url = route.request().url();
    if (url.endsWith('/metrics')) {
      return route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({
          active_goals: 0,
          total_goals: 0,
          success_rate: 0,
          avg_latency_ms: 0,
          cost_today_usd: 0,
          goals_today: 0,
        }),
      });
    }
    return route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({ goals: [] }),
    });
  });

  // Agents
  await page.route(/localhost:8000\/agents/, (route) =>
    route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify([]) })
  );

  // Connectors
  await page.route(/localhost:8000\/connectors/, (route) =>
    route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify([]) })
  );

  // Governance
  await page.route(/localhost:8000\/governance/, (route) => {
    const url = route.request().url();
    if (url.endsWith('/budget')) {
      return route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({ per_goal_usd: 1.0, per_tenant_daily_usd: 50.0 }),
      });
    }
    return route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify([]),
    });
  });

  // Knowledge / schedules / collaboration / observability / enterprise (broad catch-all)
  await page.route(/localhost:8000\/(knowledge|schedules|collaboration|observability|enterprise|marketplace|intelligence)/, (route) =>
    route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify([]) })
  );

  // Settings (LLM config + API keys)
  await page.route(/localhost:8000\/tenants\/me\/(llm|keys)/, (route) =>
    route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify([]) })
  );
}

// ── Navigation tests ──────────────────────────────────────────────────────────

test.describe('Navigation — sidebar structure', () => {
  test.beforeEach(async ({ page }) => {
    await setupAuth(page);
    await mockAllApis(page);
    await page.goto('/dashboard');
    await expect(page.locator('h1').filter({ hasText: /dashboard/i })).toBeVisible({
      timeout: 15000,
    });
  });

  test('sidebar contains "Core" section heading', async ({ page }) => {
    await expect(page.locator('aside').getByText('Core', { exact: true })).toBeVisible();
  });

  test('sidebar contains "Platform" section heading', async ({ page }) => {
    await expect(page.locator('aside').getByText('Platform', { exact: true })).toBeVisible();
  });

  test('sidebar contains "Governance" section heading', async ({ page }) => {
    await expect(page.locator('aside').getByText('Governance', { exact: true })).toBeVisible();
  });

  test('sidebar contains "Enterprise" section heading', async ({ page }) => {
    await expect(page.locator('aside').getByText('Enterprise', { exact: true })).toBeVisible();
  });

  test('all core nav links are present: Dashboard, Goals, Agents', async ({ page }) => {
    for (const label of ['Dashboard', 'Goals', 'Agents']) {
      await expect(page.locator('aside').getByText(label, { exact: true })).toBeVisible();
    }
  });

  test('all platform nav links are present: Connectors, Knowledge, Schedules, Collaboration', async ({
    page,
  }) => {
    for (const label of ['Connectors', 'Knowledge', 'Schedules', 'Collaboration']) {
      await expect(page.locator('aside').getByText(label, { exact: true })).toBeVisible();
    }
  });

  test('all governance nav links are present: Governance, Settings', async ({ page }) => {
    for (const label of ['Governance', 'Settings']) {
      await expect(page.locator('aside').getByText(label, { exact: true })).toBeVisible();
    }
  });

  test('all enterprise nav links are present: Marketplace, Observability, Eval, Enterprise', async ({
    page,
  }) => {
    for (const label of ['Marketplace', 'Observability', 'Eval', 'Enterprise']) {
      await expect(page.locator('aside').getByText(label, { exact: true })).toBeVisible();
    }
  });
});

test.describe('Navigation — clicking links', () => {
  test.beforeEach(async ({ page }) => {
    await setupAuth(page);
    await mockAllApis(page);
  });

  test('clicking Goals nav link navigates to /goals', async ({ page }) => {
    await page.goto('/dashboard');
    // NavLink renders as <a href="/goals"> — click it directly
    await page.locator('a[href="/goals"]').first().click();
    await expect(page).toHaveURL(/\/goals$/);
  });

  test('clicking Agents nav link navigates to /agents', async ({ page }) => {
    await page.goto('/dashboard');
    await page.locator('a[href="/agents"]').first().click();
    await expect(page).toHaveURL(/\/agents$/);
  });

  test('clicking Governance nav link navigates to /governance', async ({ page }) => {
    await page.goto('/dashboard');
    await page.locator('a[href="/governance"]').first().click();
    await expect(page).toHaveURL(/\/governance$/);
  });

  test('clicking Eval nav link navigates to /eval', async ({ page }) => {
    await page.goto('/dashboard');
    await page.locator('a[href="/eval"]').first().click();
    await expect(page).toHaveURL(/\/eval$/);
  });

  test('clicking Settings nav link navigates to /settings', async ({ page }) => {
    await page.goto('/dashboard');
    await page.locator('a[href="/settings"]').first().click();
    await expect(page).toHaveURL(/\/settings$/);
  });
});

test.describe('Navigation — active link highlighting', () => {
  test.beforeEach(async ({ page }) => {
    await setupAuth(page);
    await mockAllApis(page);
  });

  test('Goals nav link has blue text (active) when on /goals page', async ({ page }) => {
    await page.goto('/goals');
    // Active NavLink gets class "text-blue-400" per Sidebar.tsx
    const goalsLink = page.locator('a[href="/goals"]').first();
    await expect(goalsLink).toHaveClass(/text-blue-400/, { timeout: 10000 });
  });

  test('Dashboard nav link has blue text (active) when on /dashboard page', async ({ page }) => {
    await page.goto('/dashboard');
    const dashLink = page.locator('a[href="/dashboard"]').first();
    await expect(dashLink).toHaveClass(/text-blue-400/, { timeout: 10000 });
  });

  test('non-active links do NOT have blue text when on /dashboard', async ({ page }) => {
    await page.goto('/dashboard');
    const goalsLink = page.locator('a[href="/goals"]').first();
    await expect(goalsLink).toHaveClass(/text-gray-300/, { timeout: 10000 });
  });
});

test.describe('Navigation — page headings', () => {
  const PAGES: Array<{ path: string; heading: RegExp }> = [
    { path: '/dashboard', heading: /dashboard/i },
    { path: '/goals', heading: /goals/i },
    { path: '/governance', heading: /governance/i },
    { path: '/eval', heading: /eval/i },
  ];

  for (const { path, heading } of PAGES) {
    test(`h1 heading matches "${heading}" on ${path}`, async ({ page }) => {
      await setupAuth(page);
      await mockAllApis(page);
      await page.goto(path);
      await expect(page.locator('h1').filter({ hasText: heading })).toBeVisible({
        timeout: 15000,
      });
    });
  }

  test('unauthenticated visit to / redirects to /auth (no heading shown)', async ({ page }) => {
    await page.goto('/');
    await page.waitForLoadState('networkidle');
    await expect(page).toHaveURL(/\/(auth|login)/);
  });
});
