/**
 * Agent Lab — E2E Tests
 *
 * Covers /lab (AgentLabPage), a 4-tab experimentation lab:
 *   Tab 1: Pre-Flight Check — governance simulation + dry-run plan preview
 *   Tab 2: Live Simulation  — SSE streaming (falls back to non-streaming run)
 *   Tab 3: Prompt Lab       — prompt variant A/B testing CRUD
 *   Tab 4: Score & Benchmark — eval suite results chart + red-team run
 */
import { test, expect, type Page } from '@playwright/test';
import { setupAuth } from './helpers/auth';

// ── Mock data ─────────────────────────────────────────────────────────────────

const AGENTS = [
  { agent_id: 'agent-1', name: 'Ops Agent', autonomy_mode: 'supervised', goal_template: '' },
];

const GOV_RESULT = {
  summary: { would_block_execution: false, hitl_approvals_needed: 0 },
  policy_checks: [
    { tool: 'send_email', result: 'allow' },
    { tool: 'delete_database', result: 'deny' },
  ],
};

const SUITES = [{ suite_id: 'suite-1', name: 'Core Suite' }];

const SUITE_RESULTS = [
  { run_id: 'run-00000001', overall_score: 0.82, passed: 8, failed: 2 },
];

const VARIANTS = [
  {
    id: 'v-control',
    key: 'planner',
    name: 'Default Planner',
    prompt_text: 'Plan the goal step by step.',
    is_control: true,
    run_count: 42,
    mean_score: 0.812,
    p95_score: 0.91,
    promoted_at: null,
  },
  {
    id: 'v-challenger',
    key: 'planner',
    name: 'Concise Planner',
    prompt_text: 'Plan concisely.',
    is_control: false,
    run_count: 5,
    mean_score: 0.7,
    p95_score: 0.8,
    promoted_at: null,
  },
];

const RED_TEAM_RESULT = {
  report_id: 'rt-1',
  total: 4,
  passed: 3,
  failed: 1,
  run_at: new Date().toISOString(),
  results: [
    { case: 'jailbreak-1', passed: true },
    { case: 'prompt-injection-1', passed: true },
    { case: 'data-exfil-1', passed: true },
    { case: 'leak-secrets', passed: false, details: 'leaked' },
  ],
};

async function setupLabRoutes(
  page: Page,
  opts: { variants?: unknown[]; suites?: unknown[] } = {}
): Promise<void> {
  await page.route(/localhost:8000\/agents/, (route) =>
    route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(AGENTS) })
  );

  await page.route('**/governance/simulate', (route) =>
    route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(GOV_RESULT) })
  );

  await page.route(/localhost:8000\/goals$/, (route) => {
    if (route.request().method() === 'POST') {
      return route.fulfill({
        status: 202,
        contentType: 'application/json',
        body: JSON.stringify({ id: 'g-dry', status: 'planning', goal: 'test goal', plan: { steps: ['step 1', 'step 2'] } }),
      });
    }
    return route.continue();
  });

  await page.route('**/enterprise/simulation/available-tools', (route) =>
    route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({ tools: [{ name: 'search_web', description: 'Search the web', server_id: 'srv-1' }], total: 1 }),
    })
  );

  // SSE streaming endpoint — return non-ok so LiveSimTab falls back to the
  // plain (non-streaming) simulation run, which is far simpler to assert on.
  await page.route('**/enterprise/simulation/stream', (route) =>
    route.fulfill({ status: 404, contentType: 'application/json', body: '{"detail":"not found"}' })
  );

  await page.route('**/enterprise/simulation', (route) => {
    if (route.request().method() === 'POST') {
      return route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({
          run_id: 'sim-1',
          status: 'done',
          steps: [{ step: 1, tool: 'search_web', output: 'mocked result', mock_hit: true, cost_usd: 0.001 }],
          cost_usd: 0.001,
          iterations: 1,
          used_real_llm: false,
        }),
      });
    }
    return route.continue();
  });

  await page.route('**/intelligence/eval-suites', (route) =>
    route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(opts.suites ?? SUITES) })
  );

  await page.route(/\/intelligence\/eval-suites\/[^/]+\/results/, (route) =>
    route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(SUITE_RESULTS) })
  );

  await page.route('**/enterprise/red-team', (route) =>
    route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(RED_TEAM_RESULT) })
  );

  await page.route(/\/intelligence\/prompt-variants(\?.*)?$/, (route) => {
    const method = route.request().method();
    if (method === 'GET') {
      return route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify(opts.variants ?? VARIANTS),
      });
    }
    if (method === 'POST') {
      return route.fulfill({
        status: 201,
        contentType: 'application/json',
        body: JSON.stringify({
          id: 'v-new',
          key: 'planner',
          name: 'New Variant',
          prompt_text: 'Try something new.',
          is_control: false,
          run_count: 0,
          mean_score: null,
          p95_score: null,
          promoted_at: null,
        }),
      });
    }
    return route.continue();
  });

  await page.route(/\/intelligence\/prompt-variants\/[^/]+\/promote/, (route) =>
    route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({ id: 'v-challenger', key: 'planner', promoted: true, promoted_at: new Date().toISOString() }),
    })
  );

  await page.route(/\/intelligence\/prompt-variants\/[^/]+$/, (route) => {
    if (route.request().method() === 'DELETE') {
      return route.fulfill({ status: 204, body: '' });
    }
    return route.continue();
  });
}

test.describe('Agent Lab — Pre-Flight Check', () => {
  test('1. loads and renders the Pre-Flight tab by default', async ({ page }) => {
    await setupAuth(page);
    await setupLabRoutes(page);
    await page.goto('/lab');

    await expect(page.getByRole('heading', { name: /Agent Lab/i })).toBeVisible({ timeout: 10000 });
    await expect(page.getByRole('tab', { name: /Pre-Flight/i })).toHaveAttribute('aria-selected', 'true');
    await expect(page.getByPlaceholder(/Describe the goal to pre-flight check/i)).toBeVisible();
  });

  test('2. running a governance check shows policy results', async ({ page }) => {
    await setupAuth(page);
    await setupLabRoutes(page);
    await page.goto('/lab');

    await page.getByPlaceholder(/Describe the goal to pre-flight check/i).fill('Send a marketing email');
    await page.getByRole('button', { name: /Run Governance Check/i }).click();

    await expect(page.getByText(/Governance Analysis/i)).toBeVisible({ timeout: 8000 });
    await expect(page.getByText('send_email')).toBeVisible();
    await expect(page.getByText('delete_database')).toBeVisible();
    await expect(page.getByText('ALLOW')).toBeVisible();
    await expect(page.getByText('DENY')).toBeVisible();
  });

  test('3. previewing a dry-run plan shows the JSON plan result', async ({ page }) => {
    await setupAuth(page);
    await setupLabRoutes(page);
    await page.goto('/lab');

    await page.getByPlaceholder(/Describe the goal to pre-flight check/i).fill('Deploy the new service');
    await page.getByRole('button', { name: /Preview Plan \(Dry Run\)/i }).click();

    await expect(page.getByText(/Plan Preview/i)).toBeVisible({ timeout: 8000 });
    await expect(page.getByText(/"status": "planning"/i)).toBeVisible();
  });
});

test.describe('Agent Lab — Live Simulation', () => {
  test('4. switching to Live Sim tab shows the goal input and available tools', async ({ page }) => {
    await setupAuth(page);
    await setupLabRoutes(page);
    await page.goto('/lab');

    await page.getByRole('tab', { name: /Live Sim/i }).click();
    await expect(page.getByPlaceholder(/Goal to simulate/i)).toBeVisible();
    await expect(page.getByText('search_web')).toBeVisible({ timeout: 5000 });
  });

  test('5. running a simulation renders the resulting step timeline', async ({ page }) => {
    await setupAuth(page);
    await setupLabRoutes(page);
    await page.goto('/lab');

    await page.getByRole('tab', { name: /Live Sim/i }).click();
    await page.getByPlaceholder(/Goal to simulate/i).fill('Search for AgentVerse docs');
    await page.getByRole('button', { name: /Run Simulation/i }).click();

    await expect(page.getByText('mocked result')).toBeVisible({ timeout: 10000 });
    await expect(page.getByText(/Tool: search_web/i)).toBeVisible();
  });
});

test.describe('Agent Lab — Prompt Lab', () => {
  test('6. shows the empty state when no variants exist for a key', async ({ page }) => {
    await setupAuth(page);
    await setupLabRoutes(page, { variants: [] });
    await page.goto('/lab');

    await page.getByRole('tab', { name: /Prompt Lab/i }).click();
    await expect(page.getByText(/No variants registered/i)).toBeVisible({ timeout: 8000 });
  });

  test('7. shows control and challenger variants when populated', async ({ page }) => {
    await setupAuth(page);
    await setupLabRoutes(page);
    await page.goto('/lab');

    await page.getByRole('tab', { name: /Prompt Lab/i }).click();
    await expect(page.getByText('Default Planner')).toBeVisible({ timeout: 8000 });
    await expect(page.getByText('CONTROL')).toBeVisible();
    await expect(page.getByText('Concise Planner')).toBeVisible();
    await expect(page.getByText('CHALLENGER')).toBeVisible();
  });

  test('8. creating a new challenger variant posts and refreshes the list', async ({ page }) => {
    await setupAuth(page);
    await setupLabRoutes(page);
    await page.goto('/lab');

    await page.getByRole('tab', { name: /Prompt Lab/i }).click();
    await expect(page.getByText('Default Planner')).toBeVisible({ timeout: 8000 });

    await page.getByRole('button', { name: /Add Challenger Variant/i }).click();
    await page.getByPlaceholder(/Variant name/i).fill('New Variant');
    await page.getByPlaceholder(/Prompt text for this variant/i).fill('Try something new.');
    await page.getByRole('button', { name: /^Create$/i }).click();

    // Modal closes on success (create mutation onSuccess hides the form).
    await expect(page.getByPlaceholder(/Variant name/i)).toBeHidden({ timeout: 8000 });
  });

  test('9. promoting a challenger calls the promote endpoint', async ({ page }) => {
    let promoted = false;
    await setupAuth(page);
    await setupLabRoutes(page);
    await page.route(/\/intelligence\/prompt-variants\/[^/]+\/promote/, (route) => {
      promoted = true;
      return route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({ id: 'v-challenger', key: 'planner', promoted: true, promoted_at: new Date().toISOString() }),
      });
    });
    await page.goto('/lab');

    await page.getByRole('tab', { name: /Prompt Lab/i }).click();
    await expect(page.getByText('Concise Planner')).toBeVisible({ timeout: 8000 });
    await page.getByRole('button', { name: /^Promote$/i }).click();

    await expect(async () => expect(promoted).toBe(true)).toPass({ timeout: 5000 });
  });
});

test.describe('Agent Lab — Score & Benchmark', () => {
  test('10. shows the empty state when no eval suites exist', async ({ page }) => {
    await setupAuth(page);
    await setupLabRoutes(page, { suites: [] });
    await page.goto('/lab');

    await page.getByRole('tab', { name: /^Score$/i }).click();
    await expect(page.getByText(/No eval results/i)).toBeVisible({ timeout: 8000 });
  });

  test('11. renders the eval suite results chart when populated', async ({ page }) => {
    await setupAuth(page);
    await setupLabRoutes(page);
    await page.goto('/lab');

    await page.getByRole('tab', { name: /^Score$/i }).click();
    await expect(page.getByText(/Eval Suite Results/i)).toBeVisible({ timeout: 8000 });
    await expect(page.getByText(/Core Suite/i)).toBeVisible();
  });

  test('12. running red-team testing shows the security score and case results', async ({ page }) => {
    await setupAuth(page);
    await setupLabRoutes(page);
    await page.goto('/lab');

    await page.getByRole('tab', { name: /^Score$/i }).click();
    await page.getByRole('button', { name: /Run Red Team/i }).click();

    await expect(page.getByText('75%')).toBeVisible({ timeout: 8000 });
    await expect(page.getByText(/3 blocked/i)).toBeVisible();
    await expect(page.getByText(/1 leaked/i)).toBeVisible();
    await expect(page.getByText('leak-secrets')).toBeVisible();
    await expect(page.getByText('LEAKED')).toBeVisible();
  });
});
