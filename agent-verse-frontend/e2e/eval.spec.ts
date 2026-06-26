import { test, expect, type Page } from '@playwright/test';

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

/** Mock the supporting endpoints that EvalPage loads on mount. */
async function mockEvalSupportApis(page: Page) {
  // EvalScorerSection fetches all goals for the dropdown
  await page.route(/localhost:8000\/goals/, (route) => {
    if (route.request().method() === 'GET') {
      return route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({ goals: [] }),
      });
    }
    return route.continue();
  });

  // EvalScorerSection fetches unapplied optimization suggestions
  await page.route(/localhost:8000\/intelligence\/suggestions/, (route) =>
    route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify([]) })
  );
}

// ── Tests ─────────────────────────────────────────────────────────────────────

test.describe('Eval & Testing — page structure', () => {
  test.beforeEach(async ({ page }) => {
    await setupAuth(page);
    await mockEvalSupportApis(page);
    await page.goto('/eval');
  });

  test('renders "Eval & Testing" h1 heading and subtitle', async ({ page }) => {
    await expect(page.locator('h1').filter({ hasText: /eval/i })).toBeVisible({ timeout: 15000 });
    await expect(page.getByText('Red team testing, goal simulation, and eval scoring')).toBeVisible();
  });

  test('Red Team Testing section title is visible', async ({ page }) => {
    await expect(page.getByText('Red Team Testing')).toBeVisible({ timeout: 15000 });
  });

  test('"Run Red Team" button is visible and enabled', async ({ page }) => {
    await expect(page.getByRole('button', { name: /run red team/i })).toBeVisible({
      timeout: 15000,
    });
    await expect(page.getByRole('button', { name: /run red team/i })).toBeEnabled();
  });

  test('Goal Simulation section title is visible', async ({ page }) => {
    await expect(page.getByText('Goal Simulation')).toBeVisible({ timeout: 15000 });
  });

  test('Simulation section has Goal textarea and Mock Tools textarea', async ({ page }) => {
    await expect(
      page.locator('textarea[placeholder*="simulate" i]')
    ).toBeVisible({ timeout: 15000 });
    await expect(page.getByText('Mock Tools (JSON)')).toBeVisible();
  });

  test('Eval Scorer section title is visible', async ({ page }) => {
    await expect(page.getByText('Eval Scorer')).toBeVisible({ timeout: 15000 });
  });

  test('"No pending optimization suggestions." message shown when no suggestions', async ({
    page,
  }) => {
    await expect(page.getByText(/no pending optimization suggestions/i)).toBeVisible({
      timeout: 15000,
    });
  });
});

// ── Red Team ──────────────────────────────────────────────────────────────────

test.describe('Eval & Testing — Red Team', () => {
  test.beforeEach(async ({ page }) => {
    await setupAuth(page);
    await mockEvalSupportApis(page);
  });

  test('clicking "Run Red Team" shows Total Cases, Passed, and Failed counts', async ({ page }) => {
    const report = {
      total: 6,
      passed: 5,
      failed: 1,
      results: [
        {
          case_id: 'rt-1',
          name: 'Prompt injection resistance',
          status: 'passed',
          details: 'No injection detected',
        },
        {
          case_id: 'rt-2',
          name: 'Policy bypass attempt',
          status: 'failed',
          details: 'Shell policy was bypassed',
        },
      ],
      run_at: new Date().toISOString(),
    };

    await page.route('**/enterprise/red-team', (route) =>
      route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify(report),
      })
    );

    await page.goto('/eval');
    await page.getByRole('button', { name: /run red team/i }).click();

    await expect(page.getByText('Total Cases')).toBeVisible({ timeout: 15000 });
    await expect(page.getByText('Passed')).toBeVisible();
    await expect(page.getByText('Failed')).toBeVisible();
  });

  test('red team report shows individual case results table', async ({ page }) => {
    const report = {
      total: 2,
      passed: 1,
      failed: 1,
      results: [
        {
          case_id: 'c1',
          name: 'Prompt injection resistance',
          status: 'passed',
          details: 'No injection detected',
        },
        {
          case_id: 'c2',
          name: 'Policy bypass attempt',
          status: 'failed',
          details: 'Shell policy was bypassed',
        },
      ],
      run_at: new Date().toISOString(),
    };

    await page.route('**/enterprise/red-team', (route) =>
      route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify(report),
      })
    );

    await page.goto('/eval');
    await page.getByRole('button', { name: /run red team/i }).click();

    await expect(page.getByText('Prompt injection resistance')).toBeVisible({ timeout: 15000 });
    await expect(page.getByText('Policy bypass attempt')).toBeVisible();
    await expect(page.getByText('No injection detected')).toBeVisible();
  });

  test('red team report shows pass rate bar', async ({ page }) => {
    const report = {
      total: 4,
      passed: 3,
      failed: 1,
      results: [],
      run_at: new Date().toISOString(),
    };

    await page.route('**/enterprise/red-team', (route) =>
      route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify(report),
      })
    );

    await page.goto('/eval');
    await page.getByRole('button', { name: /run red team/i }).click();

    await expect(page.getByText('Pass rate')).toBeVisible({ timeout: 15000 });
    await expect(page.getByText('75%')).toBeVisible();
  });

  test('"Running…" text appears on button while red team request is in-flight', async ({
    page,
  }) => {
    await page.route('**/enterprise/red-team', async (route) => {
      await new Promise((resolve) => setTimeout(resolve, 500));
      await route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({ total: 0, passed: 0, failed: 0, results: [] }),
      });
    });

    await page.goto('/eval');
    await page.getByRole('button', { name: /run red team/i }).click();
    await expect(page.getByRole('button', { name: /running/i })).toBeVisible({ timeout: 3000 });
  });
});

// ── Simulation ────────────────────────────────────────────────────────────────

test.describe('Eval & Testing — Simulation', () => {
  test.beforeEach(async ({ page }) => {
    await setupAuth(page);
    await mockEvalSupportApis(page);
    await page.goto('/eval');
  });

  test('"Run Simulation" button is disabled when goal textarea is empty', async ({ page }) => {
    await expect(page.getByRole('button', { name: /run simulation/i })).toBeDisabled({
      timeout: 15000,
    });
  });

  test('"Run Simulation" button is enabled once goal text is entered', async ({ page }) => {
    await page.locator('textarea[placeholder*="simulate" i]').fill('Deploy new service to staging');
    await expect(page.getByRole('button', { name: /run simulation/i })).toBeEnabled({
      timeout: 10000,
    });
  });

  test('Mock Tools textarea defaults to "{}"', async ({ page }) => {
    const mockToolsTextarea = page.locator('textarea[placeholder*="github:list_issues" i]');
    await expect(mockToolsTextarea).toBeVisible({ timeout: 15000 });
    await expect(mockToolsTextarea).toHaveValue('{}');
  });

  test('running simulation shows "Simulated Steps" section with step items', async ({ page }) => {
    const simResult = {
      goal_id: 'sim-001',
      status: 'complete',
      steps: [
        {
          step: 'List available services',
          tool: 'k8s:list_services',
          output: '3 services found',
        },
        {
          step: 'Deploy service to staging',
          tool: 'k8s:deploy',
          output: 'Deployed successfully',
        },
      ],
      cost_usd: 0.0025,
      iterations: 2,
    };

    await page.route('**/enterprise/simulation', (route) =>
      route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify(simResult),
      })
    );

    await page.locator('textarea[placeholder*="simulate" i]').fill('Deploy new service to staging');
    await page.getByRole('button', { name: /run simulation/i }).click();

    await expect(page.getByText('Simulated Steps')).toBeVisible({ timeout: 15000 });
    await expect(page.getByText('List available services')).toBeVisible();
    await expect(page.getByText('Deploy service to staging')).toBeVisible();
  });

  test('simulation result shows status badge', async ({ page }) => {
    const simResult = {
      goal_id: 'sim-002',
      status: 'complete',
      steps: [],
      cost_usd: 0.001,
    };

    await page.route('**/enterprise/simulation', (route) =>
      route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify(simResult),
      })
    );

    await page.locator('textarea[placeholder*="simulate" i]').fill('Some goal');
    await page.getByRole('button', { name: /run simulation/i }).click();

    // Status badge renders the result.status value
    await expect(page.locator('span').filter({ hasText: 'complete' }).first()).toBeVisible({
      timeout: 15000,
    });
  });

  test('simulation result shows simulated cost', async ({ page }) => {
    const simResult = {
      status: 'complete',
      steps: [],
      cost_usd: 0.0042,
    };

    await page.route('**/enterprise/simulation', (route) =>
      route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify(simResult),
      })
    );

    await page.locator('textarea[placeholder*="simulate" i]').fill('Some goal');
    await page.getByRole('button', { name: /run simulation/i }).click();

    await expect(page.getByText(/0\.0042.*simulated cost/)).toBeVisible({ timeout: 15000 });
  });

  test('"Simulating…" text appears on button while simulation is in-flight', async ({ page }) => {
    await page.route('**/enterprise/simulation', async (route) => {
      await new Promise((resolve) => setTimeout(resolve, 500));
      await route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({ status: 'complete', steps: [] }),
      });
    });

    await page.locator('textarea[placeholder*="simulate" i]').fill('Some test goal');
    await page.getByRole('button', { name: /run simulation/i }).click();
    await expect(page.getByRole('button', { name: /simulating/i })).toBeVisible({ timeout: 3000 });
  });
});

// ── Eval Scorer ───────────────────────────────────────────────────────────────

test.describe('Eval & Testing — Eval Scorer', () => {
  test.beforeEach(async ({ page }) => {
    await setupAuth(page);
    await mockEvalSupportApis(page);
    await page.goto('/eval');
  });

  test('"Run Eval" button is disabled when no goal is selected', async ({ page }) => {
    await expect(page.getByRole('button', { name: /run eval/i })).toBeDisabled({ timeout: 15000 });
  });

  test('goal dropdown populates with goals from /goals API', async ({ page }) => {
    // Override the goals mock with one goal
    const goals = [
      {
        id: 'g-eval-01',
        goal_id: 'g-eval-01',
        goal: 'Analyse weekly sales data',
        status: 'complete',
      },
    ];

    // LIFO: registered after beforeEach mock, so it wins
    await page.route(/localhost:8000\/goals/, (route) => {
      if (route.request().method() === 'GET') {
        return route.fulfill({
          status: 200,
          contentType: 'application/json',
          body: JSON.stringify({ goals }),
        });
      }
      return route.continue();
    });

    await page.reload();

    // The dropdown should now contain the goal
    await expect(
      page.getByRole('option', { name: /analyse weekly sales data/i })
    ).toBeAttached({ timeout: 15000 });
  });

  test('shows optimization suggestions when API returns them', async ({ page }) => {
    const suggestions = [
      {
        suggestion_id: 'sug-01',
        category: 'efficiency',
        description: 'Reduce redundant tool calls by caching intermediate results.',
        confidence: 0.87,
        applied: false,
      },
    ];

    // LIFO: override the suggestions mock
    await page.route(/localhost:8000\/intelligence\/suggestions/, (route) =>
      route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify(suggestions),
      })
    );

    await page.reload();

    await expect(
      page.getByText('Reduce redundant tool calls by caching intermediate results.')
    ).toBeVisible({ timeout: 15000 });
    await expect(page.getByRole('button', { name: 'Apply' })).toBeVisible();
    await expect(page.getByRole('button', { name: 'Reject' })).toBeVisible();
  });
});
