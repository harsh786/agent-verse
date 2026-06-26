import { test, expect, type Page } from '@playwright/test';

// ── Types ──────────────────────────────────────────────────────────────────────

interface MockGoal {
  id: string;
  goal_id?: string;
  goal: string;
  status: string;
  created_at?: string;
}

// ── Shared helpers ─────────────────────────────────────────────────────────────

async function setupAuth(page: Page) {
  await page.addInitScript(() => {
    localStorage.setItem(
      'av-auth',
      JSON.stringify({
        state: {
          apiKey: 'test-key',
          tenantId: 'test-tenant',
          plan: 'free',
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
      body: JSON.stringify({ tenant_id: 'test-tenant', name: 'Test Org', plan: 'free' }),
    })
  );
}

/** Mock /agents to return empty list (GoalsListPage fetches agents for the dropdown). */
async function mockAgentsApi(page: Page, agents: unknown[] = []) {
  await page.route(/localhost:8000\/agents/, (route) =>
    route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify(agents),
    })
  );
}

/**
 * Mock the goals endpoints used by GoalsListPage.
 * A single handler dispatches on method + URL to cover:
 *   GET  /goals         → list
 *   POST /goals         → create (returns newGoal if provided)
 *   GET  /goals/{id}    → detail
 *   POST /goals/{id}/cancel → cancel
 */
async function mockGoalsApi(
  page: Page,
  {
    goals = [] as MockGoal[],
    newGoal = null as MockGoal | null,
    goalDetail = null as MockGoal | null,
  } = {}
) {
  await page.route(/localhost:8000\/goals/, async (route) => {
    const method = route.request().method();
    const url = route.request().url();

    // POST /goals/{id}/cancel
    if (method === 'POST' && url.includes('/cancel')) {
      const segments = url.split('/');
      const cancelledId = segments[segments.length - 2]; // …/goals/{id}/cancel
      const cancelled = goals.find((g) => g.id === cancelledId) ?? goals[0];
      return route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({ ...cancelled, status: 'cancelled' }),
      });
    }

    // POST /goals (submit new goal)
    if (method === 'POST') {
      return route.fulfill({
        status: 202,
        contentType: 'application/json',
        body: JSON.stringify(
          newGoal ?? { id: 'g-new', goal_id: 'g-new', status: 'planning', goal: 'New goal' }
        ),
      });
    }

    // GET /goals/{id} (detail page — url has a trailing segment after /goals/)
    if (method === 'GET' && url.match(/\/goals\/[^/]+$/)) {
      const detail =
        goalDetail ??
        goals.find((g) => url.includes(g.id)) ??
        { id: 'g-detail', goal_id: 'g-detail', goal: 'Detail goal', status: 'complete' };
      return route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify(detail),
      });
    }

    // GET /goals (list)
    return route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({ goals }),
    });
  });
}

// ── Tests ─────────────────────────────────────────────────────────────────────

test.describe('Goals', () => {
  // ── Page structure ──────────────────────────────────────────────────────────

  test('shows Goals h1 heading and page subtitle', async ({ page }) => {
    await setupAuth(page);
    await mockGoalsApi(page);
    await mockAgentsApi(page);
    await page.goto('/goals');

    await expect(page.locator('h1').filter({ hasText: /goals/i })).toBeVisible({ timeout: 15000 });
    await expect(page.getByText('Submit and track autonomous agent goals')).toBeVisible();
  });

  test('shows "Submit a new goal" form section with goal textarea', async ({ page }) => {
    await setupAuth(page);
    await mockGoalsApi(page);
    await mockAgentsApi(page);
    await page.goto('/goals');

    await expect(page.getByText('Submit a new goal')).toBeVisible({ timeout: 15000 });
    await expect(page.locator('textarea[aria-label="Goal text"]')).toBeVisible();
  });

  test('shows Agent selector dropdown defaulting to "Auto-select best agent"', async ({ page }) => {
    await setupAuth(page);
    await mockGoalsApi(page);
    await mockAgentsApi(page);
    await page.goto('/goals');

    await expect(page.getByRole('combobox', { name: /agent/i })).toBeVisible({ timeout: 15000 });
    await expect(page.getByRole('option', { name: /auto-select/i })).toBeAttached();
  });

  // ── Submit button state ─────────────────────────────────────────────────────

  test('Submit button is disabled when goal textarea is empty', async ({ page }) => {
    await setupAuth(page);
    await mockGoalsApi(page);
    await mockAgentsApi(page);
    await page.goto('/goals');
    await page.waitForLoadState('networkidle');

    const submitBtn = page.locator('button[type="submit"]');
    await expect(submitBtn).toBeDisabled({ timeout: 10000 });
  });

  test('Submit button is enabled once goal text is entered', async ({ page }) => {
    await setupAuth(page);
    await mockGoalsApi(page);
    await mockAgentsApi(page);
    await page.goto('/goals');

    await page.locator('textarea[aria-label="Goal text"]').fill('Fix all JIRA bugs');
    const submitBtn = page.locator('button[type="submit"]');
    await expect(submitBtn).toBeEnabled({ timeout: 10000 });
  });

  test('"Dry run" checkbox toggles button label between "Submit" and "Dry run"', async ({
    page,
  }) => {
    await setupAuth(page);
    await mockGoalsApi(page);
    await mockAgentsApi(page);
    await page.goto('/goals');

    // Fill text so the button is enabled
    await page.locator('textarea[aria-label="Goal text"]').fill('Some goal');

    const submitBtn = page.locator('button[type="submit"]');
    await expect(submitBtn).toContainText(/submit/i, { timeout: 10000 });

    await page.getByRole('checkbox', { name: /dry run/i }).check();
    await expect(submitBtn).toContainText(/dry run/i);
  });

  // ── Submitting a goal ───────────────────────────────────────────────────────

  test('submitting a goal navigates to the new goal detail page', async ({ page }) => {
    const created: MockGoal = {
      id: 'g-created',
      goal_id: 'g-created',
      status: 'planning',
      goal: 'Fix the critical bug',
    };

    await setupAuth(page);
    await mockGoalsApi(page, { newGoal: created });
    await mockAgentsApi(page);
    await page.goto('/goals');

    await page.locator('textarea[aria-label="Goal text"]').fill('Fix the critical bug');
    await page.locator('button[type="submit"]').click();

    // onSuccess navigates to /goals/${res.goal_id}
    await expect(page).toHaveURL(/\/goals\/g-created/, { timeout: 15000 });
  });

  // ── Goals table ─────────────────────────────────────────────────────────────

  test('goals list table shows existing goals from the API', async ({ page }) => {
    const goals: MockGoal[] = [
      {
        id: 'g1',
        goal_id: 'g1',
        goal: 'Deploy the new microservice',
        status: 'complete',
        created_at: new Date().toISOString(),
      },
      {
        id: 'g2',
        goal_id: 'g2',
        goal: 'Fix all failing unit tests',
        status: 'executing',
        created_at: new Date().toISOString(),
      },
    ];

    await setupAuth(page);
    await mockGoalsApi(page, { goals });
    await mockAgentsApi(page);
    await page.goto('/goals');

    await expect(page.getByText('Deploy the new microservice')).toBeVisible({ timeout: 15000 });
    await expect(page.getByText('Fix all failing unit tests')).toBeVisible();
  });

  test('shows "No goals found." when API returns an empty list', async ({ page }) => {
    await setupAuth(page);
    await mockGoalsApi(page, { goals: [] });
    await mockAgentsApi(page);
    await page.goto('/goals');

    await expect(page.getByText('No goals found.')).toBeVisible({ timeout: 15000 });
  });

  // ── Status filter ───────────────────────────────────────────────────────────

  test('status filter pills are visible for all statuses', async ({ page }) => {
    await setupAuth(page);
    await mockGoalsApi(page);
    await mockAgentsApi(page);
    await page.goto('/goals');

    const statuses = ['all', 'planning', 'executing', 'complete', 'failed', 'waiting_human'];
    for (const status of statuses) {
      await expect(page.getByRole('button', { name: status, exact: true })).toBeVisible({
        timeout: 15000,
      });
    }
  });

  test('filtering by "complete" hides goals with other statuses', async ({ page }) => {
    const goals: MockGoal[] = [
      {
        id: 'g1',
        goal: 'Complete goal text',
        status: 'complete',
        created_at: new Date().toISOString(),
      },
      {
        id: 'g2',
        goal: 'Planning goal text',
        status: 'planning',
        created_at: new Date().toISOString(),
      },
    ];

    await setupAuth(page);
    await mockGoalsApi(page, { goals });
    await mockAgentsApi(page);
    await page.goto('/goals');

    // Both goals visible before filtering
    await expect(page.getByText('Complete goal text')).toBeVisible({ timeout: 15000 });
    await expect(page.getByText('Planning goal text')).toBeVisible();

    // Apply "complete" filter (client-side)
    await page.getByRole('button', { name: 'complete', exact: true }).click();

    await expect(page.getByText('Complete goal text')).toBeVisible();
    await expect(page.getByText('Planning goal text')).not.toBeVisible();
  });

  test('search input filters goals by text', async ({ page }) => {
    const goals: MockGoal[] = [
      {
        id: 'g1',
        goal: 'Deploy kubernetes cluster',
        status: 'complete',
        created_at: new Date().toISOString(),
      },
      {
        id: 'g2',
        goal: 'Fix authentication flow',
        status: 'complete',
        created_at: new Date().toISOString(),
      },
    ];

    await setupAuth(page);
    await mockGoalsApi(page, { goals });
    await mockAgentsApi(page);
    await page.goto('/goals');

    await expect(page.getByText('Deploy kubernetes cluster')).toBeVisible({ timeout: 15000 });

    // Type into search input
    await page.getByRole('searchbox', { name: /search goals/i }).fill('kubernetes');

    await expect(page.getByText('Deploy kubernetes cluster')).toBeVisible();
    await expect(page.getByText('Fix authentication flow')).not.toBeVisible();
  });

  // ── Cancel button ───────────────────────────────────────────────────────────

  test('Cancel button appears for "executing" goals', async ({ page }) => {
    const goals: MockGoal[] = [
      {
        id: 'g1',
        goal: 'Running goal',
        status: 'executing',
        created_at: new Date().toISOString(),
      },
    ];

    await setupAuth(page);
    await mockGoalsApi(page, { goals });
    await mockAgentsApi(page);
    await page.goto('/goals');

    await expect(page.getByRole('button', { name: /cancel goal/i })).toBeVisible({
      timeout: 15000,
    });
  });

  test('Cancel button appears for "planning" goals', async ({ page }) => {
    const goals: MockGoal[] = [
      {
        id: 'g1',
        goal: 'Goal being planned',
        status: 'planning',
        created_at: new Date().toISOString(),
      },
    ];

    await setupAuth(page);
    await mockGoalsApi(page, { goals });
    await mockAgentsApi(page);
    await page.goto('/goals');

    await expect(page.getByRole('button', { name: /cancel goal/i })).toBeVisible({
      timeout: 15000,
    });
  });

  test('Cancel button does NOT appear for "complete" goals', async ({ page }) => {
    const goals: MockGoal[] = [
      {
        id: 'g1',
        goal: 'Finished goal',
        status: 'complete',
        created_at: new Date().toISOString(),
      },
    ];

    await setupAuth(page);
    await mockGoalsApi(page, { goals });
    await mockAgentsApi(page);
    await page.goto('/goals');

    await expect(page.getByText('Finished goal')).toBeVisible({ timeout: 15000 });
    await expect(page.getByRole('button', { name: /cancel goal/i })).not.toBeVisible();
  });

  // ── Navigation to goal detail ───────────────────────────────────────────────

  test('clicking a goal row navigates to its detail page', async ({ page }) => {
    const goals: MockGoal[] = [
      {
        id: 'goal-row-click',
        goal_id: 'goal-row-click',
        goal: 'Build the new feature',
        status: 'complete',
        created_at: new Date().toISOString(),
      },
    ];

    await setupAuth(page);
    await mockGoalsApi(page, {
      goals,
      goalDetail: {
        id: 'goal-row-click',
        goal_id: 'goal-row-click',
        goal: 'Build the new feature',
        status: 'complete',
      },
    });
    await mockAgentsApi(page);
    await page.goto('/goals');

    await page.getByText('Build the new feature').click();
    await expect(page).toHaveURL(/\/goals\/goal-row-click/, { timeout: 15000 });
  });
});
