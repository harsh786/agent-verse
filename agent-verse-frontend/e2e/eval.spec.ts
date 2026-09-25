import { test, expect, type Page } from '@playwright/test';

// ── Shared helpers ─────────────────────────────────────────────────────────────

async function setupAuth(page: Page) {
  // Catch-all FIRST — blocks any unmocked localhost:8000 calls from triggering logout
  await page.route(/localhost:8000/, (route) =>
    route.fulfill({ status: 404, contentType: 'application/json', body: JSON.stringify({ detail: 'not found' }) })
  );
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
    sessionStorage.setItem(
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
  });
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

  // Suggestions
  await page.route(/localhost:8000\/intelligence\/suggestions/, (route) =>
    route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify([]) })
  );
  // Governance approvals stream
  await page.route('**/governance/approvals/stream', (route) =>
    route.fulfill({ status: 200, contentType: 'text/event-stream', body: '' })
  );
  await page.route('**/governance/approvals', (route) =>
    route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify([]) })
  );
}


/**
 * Goal simulation is an SSE STREAM, not a JSON response: SimulationTab POSTs to
 * /enterprise/simulation/stream and reads the body with a ReadableStream reader,
 * parsing `data: {...}` chunks separated by a blank line.  Mocking a plain JSON
 * object on /enterprise/simulation therefore streamed nothing and no step, badge
 * or cost ever rendered.
 */
function sseBody(...events: Record<string, unknown>[]): string {
  return events.map((e) => `data: ${JSON.stringify(e)}\n\n`).join('');
}

const SIM_STREAM = '**/enterprise/simulation/stream';

// ── Tests ─────────────────────────────────────────────────────────────────────

/**
 * EvalPage is a TABBED page (Scorecard | Simulation | Red Team | Suites) and
 * opens on Scorecard.  Red-team and simulation controls do not exist in the DOM
 * until their tab is selected, so every spec that touches them must open the tab
 * first — asserting straight after goto('/eval') only ever saw the Scorecard.
 */
async function openEvalTab(
  page: Page,
  name: 'Scorecard' | 'Simulation' | 'Red Team' | 'Suites'
): Promise<void> {
  await page.goto('/eval');
  await page.getByRole('tab', { name }).click();
}

test.describe('Eval & Testing — page structure', () => {
  test.beforeEach(async ({ page }) => {
    await setupAuth(page);
    await mockEvalSupportApis(page);
    await page.goto('/eval');
  });

  test('renders "Eval & Testing" h1 heading and subtitle', async ({ page }) => {
    await expect(page.locator('h1').filter({ hasText: /eval/i })).toBeVisible({ timeout: 15000 });
    await expect(
      page.getByText('7-dimension scoring, goal simulation, red team testing, and eval suites')
    ).toBeVisible();
  });

  test('Red Team Testing section title is visible', async ({ page }) => {
    await openEvalTab(page, 'Red Team');
    await expect(page.locator('h3').filter({ hasText: 'Red Team Testing' })).toBeVisible({ timeout: 15000 });
  });

  test('"Launch Red Team Suite" button is visible and enabled', async ({ page }) => {
    await openEvalTab(page, 'Red Team');
    await expect(page.getByRole('button', { name: /launch red team suite/i })).toBeVisible({
      timeout: 15000,
    });
    await expect(page.getByRole('button', { name: /launch red team suite/i })).toBeEnabled();
  });

  test('Simulation tab is reachable and shows the Goal field', async ({ page }) => {
    await openEvalTab(page, 'Simulation');
    await expect(page.getByRole('tab', { name: 'Simulation' })).toHaveAttribute(
      'aria-selected',
      'true'
    );
  });

  test('Simulation section has Goal textarea and Mock Tool Responses textarea', async ({ page }) => {
    await openEvalTab(page, 'Simulation');
    await expect(
      page.locator('textarea[placeholder*="simulate" i]')
    ).toBeVisible({ timeout: 15000 });
    await expect(page.getByText('Mock Tool Responses (JSON)')).toBeVisible();
  });

  test('Scorecard tab is the default and exposes the Run Eval control', async ({ page }) => {
    await expect(page.getByRole('tab', { name: 'Scorecard' })).toHaveAttribute(
      'aria-selected',
      'true'
    );
    await expect(page.getByRole('button', { name: /run eval/i })).toBeVisible({ timeout: 15000 });
  });
});

// ── Red Team ──────────────────────────────────────────────────────────────────

test.describe('Eval & Testing — Red Team', () => {
  test.beforeEach(async ({ page }) => {
    await setupAuth(page);
    await mockEvalSupportApis(page);
  });

  test('clicking "Launch Red Team Suite" shows Total Cases, Blocked, and Leaked counts', async ({ page }) => {
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

    await openEvalTab(page, 'Red Team');
    await page.getByRole('button', { name: /launch red team suite/i }).click();

    await expect(page.getByText('Total Cases')).toBeVisible({ timeout: 15000 });
    await expect(page.getByText('Blocked').first()).toBeVisible();
    await expect(page.getByText('Leaked', { exact: true }).first()).toBeVisible();
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

    await openEvalTab(page, 'Red Team');
    await page.getByRole('button', { name: /launch red team suite/i }).click();

    // The case row renders r.name / r.attack_vector / r.risk_level — `details`
    // is not displayed anywhere, so assert on what the table actually shows.
    await expect(page.getByText('Prompt injection resistance')).toBeVisible({ timeout: 15000 });
    await expect(page.getByText('Policy bypass attempt')).toBeVisible();
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

    await openEvalTab(page, 'Red Team');
    await page.getByRole('button', { name: /launch red team suite/i }).click();

    await expect(page.getByText(/attack vectors blocked/i)).toBeVisible({ timeout: 15000 });
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

    await openEvalTab(page, 'Red Team');
    await page.getByRole('button', { name: /launch red team suite/i }).click();
    await expect(page.getByRole('button', { name: /running/i })).toBeVisible({ timeout: 3000 });
  });
});

// ── Simulation ────────────────────────────────────────────────────────────────

test.describe('Eval & Testing — Simulation', () => {
  test.beforeEach(async ({ page }) => {
    await setupAuth(page);
    await mockEvalSupportApis(page);
    await openEvalTab(page, 'Simulation');
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

  test('running simulation shows "Execution Steps" section with step items', async ({ page }) => {
    await page.route(SIM_STREAM, (route) =>
      route.fulfill({
        status: 200,
        contentType: 'text/event-stream',
        body: sseBody(
          { type: 'simulation_step', step: 1, tool: 'k8s:list_services', output: '3 services found' },
          { type: 'simulation_step', step: 2, tool: 'k8s:deploy', output: 'Deployed successfully' },
          { type: 'simulation_complete', status: 'complete', cost_usd: 0.0025 }
        ),
      })
    );

    await page.locator('textarea[placeholder*="simulate" i]').fill('Deploy new service to staging');
    await page.getByRole('button', { name: /run simulation/i }).click();

    await expect(page.getByText('Execution Steps')).toBeVisible({ timeout: 15000 });
    await expect(page.getByText('k8s:list_services')).toBeVisible();
    await expect(page.getByText('k8s:deploy')).toBeVisible();
  });

  test('simulation result shows status badge', async ({ page }) => {
    await page.route(SIM_STREAM, (route) =>
      route.fulfill({
        status: 200,
        contentType: 'text/event-stream',
        body: sseBody({ type: 'simulation_complete', status: 'complete', cost_usd: 0.001 }),
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
    await page.route(SIM_STREAM, (route) =>
      route.fulfill({
        status: 200,
        contentType: 'text/event-stream',
        body: sseBody({ type: 'simulation_complete', status: 'complete', cost_usd: 0.0042 }),
      })
    );

    await page.locator('textarea[placeholder*="simulate" i]').fill('Some goal');
    await page.getByRole('button', { name: /run simulation/i }).click();

    await expect(page.getByText('$0.0042')).toBeVisible({ timeout: 15000 });
  });

  test('"Simulating…" text appears on button while simulation is in-flight', async ({ page }) => {
    await page.route(SIM_STREAM, async (route) => {
      await new Promise((resolve) => setTimeout(resolve, 1500));
      await route.fulfill({
        status: 200,
        contentType: 'text/event-stream',
        body: sseBody({ type: 'simulation_complete', status: 'complete' }),
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

  // Optimization suggestions moved off the eval page: /intelligence/suggestions
  // is now rendered by SelfImprovementPage at /self-improvement.  EvalPage has
  // no suggestions UI at all, so this assertion has to follow the feature.
  test('shows optimization suggestions when API returns them', async ({ page }) => {
    const suggestions = [
      {
        // Matches the Suggestion contract in lib/api/client.ts — the old shape
        // here (suggestion_id/category/applied) has no `type`, and the row
        // renderer calls s.type.replace(...).
        id: 'sug-01',
        type: 'efficiency',
        description: 'Reduce redundant tool calls by caching intermediate results.',
        confidence: 0.87,
        status: 'pending',
        created_at: new Date().toISOString(),
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

    // SelfImprovementPage is tabbed and opens on "experiments"; suggestions
    // live behind their own tab.
    await page.goto('/self-improvement');
    await page.getByRole('tab', { name: /suggestions/i }).click();

    await expect(
      page.getByText('Reduce redundant tool calls by caching intermediate results.')
    ).toBeVisible({ timeout: 15000 });
    // exact: true — the status FILTER chips are also buttons ("applied",
    // "rejected"), so a substring match resolves to two elements.
    await expect(page.getByRole('button', { name: 'Apply', exact: true })).toBeVisible();
    await expect(page.getByRole('button', { name: 'Reject', exact: true })).toBeVisible();
  });
});
