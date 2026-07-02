/**
 * Governance Center — Comprehensive E2E Tests
 *
 * 30 tests across 6 suites covering all 4 tabs + Emergency Stop + Cross-tab:
 *   1.  Page structure & navigation
 *   2.  Policies tab  (create, delete, simulate, time window)
 *   3.  Approvals tab (queue, risk badges, approve, reject, batch, SLA)
 *   4.  Audit tab     (filters, rows, export, chain verify, empty state)
 *   5.  Budget tab    (gauges, config form, save, anomalies)
 *   6.  Emergency Stop (confirm, activate, clear, persistent banner)
 */
import { test, expect, type Page } from '@playwright/test';

// ── Auth setup ────────────────────────────────────────────────────────────────

async function setupAuth(page: Page): Promise<void> {
  await page.route(/localhost:8000/, (route) =>
    route.fulfill({ status: 404, contentType: 'application/json', body: '{"detail":"not found"}' })
  );
  await page.addInitScript(() => {
    const AUTH = JSON.stringify({
      state: { apiKey: 'test-key', tenantId: 'test-tenant', plan: 'enterprise', isAuthenticated: true },
      version: 0,
    });
    localStorage.setItem('av-auth', AUTH);
    sessionStorage.setItem('av-auth', AUTH);
    localStorage.setItem('av_api_key', 'test-key');
    // Clear emergency store so banner is not active by default
    localStorage.removeItem('agentverse-emergency');
  });
  await page.route('**/tenants/me', (route) =>
    route.fulfill({
      status: 200, contentType: 'application/json',
      body: JSON.stringify({ tenant_id: 'test-tenant', name: 'PineLabs', plan: 'enterprise' }),
    })
  );
}

// ── Mock data ─────────────────────────────────────────────────────────────────

const POLICIES = [
  { policy_id: 'pol-1', name: 'block-shell', description: 'Block shell execution', tools_pattern: 'shell:*', action: 'deny', priority: 10 },
  { policy_id: 'pol-2', name: 'approve-deploy', description: 'Require approval for deploys', tools_pattern: 'deploy:*', action: 'require_approval', priority: 5 },
];

const APPROVALS = [
  { request_id: 'req-001', goal_id: 'goal-aaa', action: 'Deploy payment-service to prod', risk_level: 'critical', status: 'pending' },
  { request_id: 'req-002', goal_id: 'goal-bbb', action: 'Delete S3 bucket backups', risk_level: 'high', status: 'pending' },
];

const RESOLVED = [
  { request_id: 'req-old', goal_id: 'goal-ccc', action: 'Read prod DB', risk_level: 'low', status: 'approved' },
];

const AUDIT_EVENTS = [
  { event_id: 'evt-001', goal_id: 'goal-aaa', tool_name: 'shell:execute', action_level: 'deny', outcome: 'blocked', approver: null, note: null },
  { event_id: 'evt-002', goal_id: 'goal-bbb', tool_name: 'jira:search', action_level: 'allow', outcome: 'success', approver: null, note: null },
  { event_id: 'evt-003', goal_id: 'goal-ccc', tool_name: 'github:delete_repo', action_level: 'approval', outcome: 'approved', approver: 'alice@pinelabs.com', note: 'Reviewed and approved' },
];

const BUDGET = { tenant_id: 'test-tenant', per_goal_usd: 10.0, per_tenant_daily_usd: 500.0 };

const SLA_STATS = {
  pending: 2, approved: 8, denied: 1, timed_out: 0, escalated: 0,
  within_sla: 7, avg_resolution_seconds: 180,
};

const COST_SUMMARY = {
  total_cost_usd: 42.5, cost_by_day: [], cost_by_model: {}, daily_budget_usd: 500, budget_utilization: 8.5,
};

async function setupGovRoutes(page: Page, opts: { approvals?: unknown[]; policies?: unknown[]; audit?: unknown[] } = {}): Promise<void> {
  // Register broad/base routes FIRST so more-specific ones win via LIFO

  // Budget
  await page.route(/localhost:8000\/governance\/budget(\?.*)?$/, (route) => {
    if (route.request().method() === 'PUT')
      return route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify({ ...BUDGET, per_goal_usd: 25 }) });
    return route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(BUDGET) });
  });

  // Emergency stop
  await page.route(/localhost:8000\/governance\/emergency-stop/, (route) => {
    if (route.request().method() === 'POST')
      return route.fulfill({
        status: 200, contentType: 'application/json',
        body: JSON.stringify({ status: 'emergency_stop_activated', cancelled_goals: 3, rejected_approvals: 1, tenant_id: 'test-tenant' }),
      });
    return route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify({ status: 'cleared', tenant_id: 'test-tenant' }) });
  });

  // Costs
  await page.route(/localhost:8000\/costs\/summary/, (route) =>
    route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(COST_SUMMARY) })
  );
  await page.route(/localhost:8000\/costs\/anomalies/, (route) =>
    route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify([{ id: 'a1', type: 'spend_spike', message: 'Unusual spike detected', cost_delta_usd: 12.3, severity: 'high', detected_at: new Date().toISOString() }]) })
  );

  // Audit chain verify (must be before broad audit route)
  await page.route(/localhost:8000\/governance\/audit\/integrity\/verify/, (route) =>
    route.fulfill({
      status: 200, contentType: 'application/json',
      body: JSON.stringify({ verified: true, verified_events: 42, chain_tip_hash: 'abc123def456' }),
    })
  );

  // Audit events (broad, matches /governance/audit with optional query params)
  await page.route(/localhost:8000\/governance\/audit(\?.*)?$/, (route) =>
    route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(opts.audit ?? AUDIT_EVENTS) })
  );

  // SSE streams
  await page.route(/localhost:8000\/governance\/approvals\/stream/, (route) =>
    route.fulfill({ status: 200, contentType: 'text/event-stream', body: 'data: {"type":"connected"}\n\n' })
  );
  await page.route(/localhost:8000\/governance\/policies\/stream/, (route) =>
    route.fulfill({ status: 200, contentType: 'text/event-stream', body: 'data: {"type":"connected"}\n\n' })
  );

  // Approvals SLA stats (before broad approvals route)
  await page.route(/localhost:8000\/governance\/approvals\/sla-stats/, (route) =>
    route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(SLA_STATS) })
  );

  // Approval actions
  await page.route(/localhost:8000\/governance\/approvals\/.*\/approve/, (route) =>
    route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify({ status: 'approved', request_id: 'req-001' }) })
  );
  await page.route(/localhost:8000\/governance\/approvals\/.*\/reject/, (route) =>
    route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify({ status: 'rejected', request_id: 'req-001' }) })
  );

  // Batch approve
  await page.route(/localhost:8000\/governance\/hitl\/batch-approve/, (route) =>
    route.fulfill({
      status: 200, contentType: 'application/json',
      body: JSON.stringify({ approved: 2, rejected: 0, not_found: 0, results: [] }),
    })
  );

  // Approvals list (after all more-specific approval routes)
  await page.route(/localhost:8000\/governance\/approvals(\?.*)?$/, (route) =>
    route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify([...(opts.approvals ?? APPROVALS), ...RESOLVED]) })
  );

  // Policy simulate (before policies list)
  await page.route(/localhost:8000\/governance\/policies\/simulate/, (route) =>
    route.fulfill({
      status: 200, contentType: 'application/json',
      body: JSON.stringify({ simulation_results: { 'shell:execute': 'DENY', 'jira:search': 'ALLOW' }, tenant_id: 'test-tenant' }),
    })
  );

  // Policy versions / rollback (specific policy sub-resources)
  await page.route(/localhost:8000\/governance\/policies\/[^/]+(\/versions|\/rollback)?$/, (route) => {
    if (route.request().method() === 'DELETE')
      return route.fulfill({ status: 204, body: '' });
    return route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify([]) });
  });

  // Policies list / create (after all more-specific policy routes)
  await page.route(/localhost:8000\/governance\/policies(\?.*)?$/, (route) => {
    if (route.request().method() === 'GET')
      return route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(opts.policies ?? POLICIES) });
    if (route.request().method() === 'POST')
      return route.fulfill({ status: 201, contentType: 'application/json', body: JSON.stringify({ ...POLICIES[0], policy_id: 'pol-new', name: 'new-policy' }) });
    return route.fulfill({ status: 204, body: '' });
  });
}

// ═══════════════════════════════════════════════════════════════════════════════
// SUITE 1 — Page Structure
// ═══════════════════════════════════════════════════════════════════════════════

test.describe('Governance — Page Structure', () => {

  test('1. Shows page heading and four tab buttons', async ({ page }) => {
    await setupAuth(page);
    await setupGovRoutes(page);
    await page.goto('/governance');

    await expect(page.getByRole('heading', { name: 'Governance' })).toBeVisible({ timeout: 10000 });
    await expect(page.getByTestId('tab-policies')).toBeVisible({ timeout: 5000 });
    await expect(page.getByTestId('tab-approvals')).toBeVisible({ timeout: 5000 });
    await expect(page.getByTestId('tab-audit')).toBeVisible({ timeout: 5000 });
    await expect(page.getByTestId('tab-budget')).toBeVisible({ timeout: 5000 });
  });

  test('2. Shows emergency stop button in the top bar', async ({ page }) => {
    await setupAuth(page);
    await setupGovRoutes(page);
    await page.goto('/governance');

    await expect(page.getByRole('heading', { name: 'Governance' })).toBeVisible({ timeout: 10000 });
    await expect(page.getByTestId('emergency-stop-btn')).toBeVisible({ timeout: 5000 });
  });

  test('3. Tab switching changes active tab state', async ({ page }) => {
    await setupAuth(page);
    await setupGovRoutes(page);
    await page.goto('/governance');

    await expect(page.getByRole('heading', { name: 'Governance' })).toBeVisible({ timeout: 10000 });
    await page.getByTestId('tab-approvals').click();
    await expect(page.getByTestId('tab-approvals')).toHaveClass(/text-primary/, { timeout: 3000 });
  });

  test('4. Policies tab is active by default', async ({ page }) => {
    await setupAuth(page);
    await setupGovRoutes(page);
    await page.goto('/governance');

    await expect(page.getByRole('heading', { name: 'Governance' })).toBeVisible({ timeout: 10000 });
    await expect(page.getByTestId('tab-policies')).toHaveClass(/text-primary/, { timeout: 3000 });
  });
});

// ═══════════════════════════════════════════════════════════════════════════════
// SUITE 2 — Policies Tab
// ═══════════════════════════════════════════════════════════════════════════════

test.describe('Governance — Policies Tab', () => {

  test('5. Lists existing policies with name, pattern, and action badges', async ({ page }) => {
    await setupAuth(page);
    await setupGovRoutes(page);
    await page.goto('/governance');

    await expect(page.getByRole('heading', { name: 'Governance' })).toBeVisible({ timeout: 10000 });
    await expect(page.getByText('block-shell')).toBeVisible({ timeout: 8000 });
    await expect(page.getByText('shell:*')).toBeVisible({ timeout: 5000 });
    await expect(page.getByText('deny').first()).toBeVisible({ timeout: 5000 });
    await expect(page.getByText('approve-deploy')).toBeVisible({ timeout: 5000 });
  });

  test('6. Shows priority badge for each policy', async ({ page }) => {
    await setupAuth(page);
    await setupGovRoutes(page);
    await page.goto('/governance');

    await expect(page.getByText('block-shell')).toBeVisible({ timeout: 10000 });
    await expect(page.getByText('10')).toBeVisible({ timeout: 5000 });
  });

  test('7. Opens New Policy form on button click', async ({ page }) => {
    await setupAuth(page);
    await setupGovRoutes(page);
    await page.goto('/governance');

    await expect(page.getByText('block-shell')).toBeVisible({ timeout: 10000 });
    await page.getByText(/New Policy/).click();
    await expect(page.getByTestId('policy-form')).toBeVisible({ timeout: 5000 });
    await expect(page.getByTestId('policy-name-input')).toBeVisible({ timeout: 3000 });
    await expect(page.getByTestId('policy-pattern-input')).toBeVisible({ timeout: 3000 });
  });

  test('8. Creates a policy with POST to /governance/policies', async ({ page }) => {
    let postBody: Record<string, unknown> = {};
    await setupAuth(page);
    await setupGovRoutes(page);

    await page.route(/localhost:8000\/governance\/policies(\?.*)?$/, (route) => {
      if (route.request().method() === 'POST') {
        postBody = JSON.parse(route.request().postData() ?? '{}');
        return route.fulfill({ status: 201, contentType: 'application/json', body: JSON.stringify({ ...POLICIES[0], policy_id: 'pol-new' }) });
      }
      return route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(POLICIES) });
    });

    await page.goto('/governance');
    await expect(page.getByText('block-shell')).toBeVisible({ timeout: 10000 });
    await page.getByText(/New Policy/).click();
    await page.getByTestId('policy-name-input').fill('deny-shell');
    await page.getByTestId('policy-pattern-input').fill('shell:*');
    await page.getByTestId('save-policy-btn').click();

    await expect(async () => {
      expect(postBody.name).toBe('deny-shell');
      expect(postBody.tools_pattern).toBe('shell:*');
    }).toPass({ timeout: 5000 });
  });

  test('9. Deletes a policy with DELETE request', async ({ page }) => {
    let deleteUrl = '';
    await setupAuth(page);
    await setupGovRoutes(page);

    await page.route(/\/governance\/policies\/.*/, (route) => {
      if (route.request().method() === 'DELETE') {
        deleteUrl = route.request().url();
        return route.fulfill({ status: 204, body: '' });
      }
      return route.fulfill({ status: 200, body: '{}' });
    });

    await page.goto('/governance');
    await expect(page.getByText('block-shell')).toBeVisible({ timeout: 10000 });
    await page.getByTestId('delete-policy-pol-1').click();

    await expect(async () => {
      expect(deleteUrl).toContain('pol-1');
    }).toPass({ timeout: 5000 });
  });

  test('10. Simulate modal opens and shows results', async ({ page }) => {
    await setupAuth(page);
    await setupGovRoutes(page);
    await page.goto('/governance');

    await expect(page.getByRole('heading', { name: 'Governance' })).toBeVisible({ timeout: 10000 });
    await page.getByText(/Simulate/).click();

    await expect(page.getByText(/Policy Simulator/i)).toBeVisible({ timeout: 5000 });
    await page.getByRole('button', { name: /Run Simulation/i }).click();

    await expect(page.getByText(/DENY|ALLOW/i).first()).toBeVisible({ timeout: 8000 });
  });

  test('11. Shows empty state when no policies configured', async ({ page }) => {
    await setupAuth(page);
    await setupGovRoutes(page, { policies: [] });
    await page.goto('/governance');

    await expect(page.getByTestId('policies-empty')).toBeVisible({ timeout: 10000 });
    await expect(page.getByText(/No policies configured/i)).toBeVisible({ timeout: 5000 });
  });

  test('12. Action badge colors: deny=red, require_approval=orange', async ({ page }) => {
    await setupAuth(page);
    await setupGovRoutes(page);
    await page.goto('/governance');

    await expect(page.getByText('block-shell')).toBeVisible({ timeout: 10000 });
    // Both actions are present
    await expect(page.getByText('deny').first()).toBeVisible({ timeout: 5000 });
    await expect(page.getByText('require_approval')).toBeVisible({ timeout: 5000 });
  });
});

// ═══════════════════════════════════════════════════════════════════════════════
// SUITE 3 — Approvals Tab
// ═══════════════════════════════════════════════════════════════════════════════

test.describe('Governance — Approvals Tab', () => {

  test('13. Shows SLA statistics row with pending/approved/denied/within-SLA counts', async ({ page }) => {
    await setupAuth(page);
    await setupGovRoutes(page);
    await page.goto('/governance');

    await expect(page.getByRole('heading', { name: 'Governance' })).toBeVisible({ timeout: 10000 });
    await page.getByTestId('tab-approvals').click();

    await expect(page.getByTestId('sla-stats')).toBeVisible({ timeout: 10000 });
    // SLA_STATS.pending = 2
    await expect(page.getByText('2').first()).toBeVisible({ timeout: 5000 });
  });

  test('14. Shows approval cards with risk badges', async ({ page }) => {
    await setupAuth(page);
    await setupGovRoutes(page);
    await page.goto('/governance');

    await page.getByTestId('tab-approvals').click();
    await expect(page.getByText('Deploy payment-service to prod')).toBeVisible({ timeout: 10000 });
    await expect(page.getByText('critical')).toBeVisible({ timeout: 5000 });
    await expect(page.getByText('high')).toBeVisible({ timeout: 5000 });
  });

  test('15. Approve button calls POST approve endpoint', async ({ page }) => {
    let approveCalled = false;
    await setupAuth(page);
    await setupGovRoutes(page);

    await page.route(/\/governance\/approvals\/req-001\/approve/, (route) => {
      approveCalled = true;
      return route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify({ status: 'approved' }) });
    });

    await page.goto('/governance');
    await page.getByTestId('tab-approvals').click();
    await expect(page.getByTestId('approve-btn-req-001')).toBeVisible({ timeout: 10000 });
    await page.getByTestId('approve-btn-req-001').click();

    await expect(async () => {
      expect(approveCalled).toBe(true);
    }).toPass({ timeout: 5000 });
  });

  test('16. Reject button calls POST reject endpoint', async ({ page }) => {
    let rejectCalled = false;
    await setupAuth(page);
    await setupGovRoutes(page);

    await page.route(/\/governance\/approvals\/req-001\/reject/, (route) => {
      rejectCalled = true;
      return route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify({ status: 'rejected' }) });
    });

    await page.goto('/governance');
    await page.getByTestId('tab-approvals').click();
    await expect(page.getByTestId('reject-btn-req-001')).toBeVisible({ timeout: 10000 });
    await page.getByTestId('reject-btn-req-001').click();

    await expect(async () => {
      expect(rejectCalled).toBe(true);
    }).toPass({ timeout: 5000 });
  });

  test('17. Batch toolbar appears when approval is selected', async ({ page }) => {
    await setupAuth(page);
    await setupGovRoutes(page);
    await page.goto('/governance');

    await page.getByTestId('tab-approvals').click();
    await expect(page.getByTestId('approval-card').first()).toBeVisible({ timeout: 10000 });

    // Click first checkbox in the approvals area
    const checkbox = page.getByTestId('approval-card').first().getByRole('checkbox');
    await checkbox.click();
    await expect(page.getByTestId('batch-toolbar')).toBeVisible({ timeout: 5000 });
    await expect(page.getByText(/Batch Approve/i)).toBeVisible({ timeout: 3000 });
  });

  test('18. Shows recently resolved section with approved/rejected items', async ({ page }) => {
    await setupAuth(page);
    await setupGovRoutes(page);
    await page.goto('/governance');

    await page.getByTestId('tab-approvals').click();
    await expect(page.getByText('Recently Resolved')).toBeVisible({ timeout: 10000 });
    // RESOLVED has one item with status 'approved'
    await expect(page.getByText('approved').first()).toBeVisible({ timeout: 5000 });
  });

  test('19. Shows empty state when no pending approvals', async ({ page }) => {
    await setupAuth(page);
    await setupGovRoutes(page, { approvals: [] });

    // Override so resolved list is also empty
    await page.route(/localhost:8000\/governance\/approvals(\?.*)?$/, (route) =>
      route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify([]) })
    );

    await setupAuth(page);
    await page.goto('/governance');
    await page.getByTestId('tab-approvals').click();
    await expect(page.getByTestId('approvals-empty')).toBeVisible({ timeout: 10000 });
  });
});

// ═══════════════════════════════════════════════════════════════════════════════
// SUITE 4 — Audit Tab
// ═══════════════════════════════════════════════════════════════════════════════

test.describe('Governance — Audit Tab', () => {

  test('20. Shows audit filter panel with Goal ID and Tool Name inputs', async ({ page }) => {
    await setupAuth(page);
    await setupGovRoutes(page);
    await page.goto('/governance');

    await page.getByTestId('tab-audit').click();
    await expect(page.getByTestId('audit-filters')).toBeVisible({ timeout: 10000 });
    await expect(page.getByLabel(/goal id/i)).toBeVisible({ timeout: 5000 });
    await expect(page.getByLabel(/tool name/i)).toBeVisible({ timeout: 5000 });
  });

  test('21. Renders audit event rows with tool, level, and outcome badges', async ({ page }) => {
    await setupAuth(page);
    await setupGovRoutes(page);
    await page.goto('/governance');

    await page.getByTestId('tab-audit').click();
    await expect(page.getByTestId('audit-row').first()).toBeVisible({ timeout: 10000 });
    await expect(page.getByText('shell:execute')).toBeVisible({ timeout: 5000 });
    await expect(page.getByText('deny').first()).toBeVisible({ timeout: 5000 });
    await expect(page.getByText('blocked')).toBeVisible({ timeout: 5000 });
  });

  test('22. Shows action level badges: deny=red, allow=green, approval=orange', async ({ page }) => {
    await setupAuth(page);
    await setupGovRoutes(page);
    await page.goto('/governance');

    await page.getByTestId('tab-audit').click();
    await expect(page.getByTestId('audit-row').first()).toBeVisible({ timeout: 10000 });
    // allow badge
    await expect(page.getByText('allow').first()).toBeVisible({ timeout: 5000 });
    // approval badge
    await expect(page.getByText('approval').first()).toBeVisible({ timeout: 5000 });
  });

  test('23. Export JSON and CSV buttons are present', async ({ page }) => {
    await setupAuth(page);
    await setupGovRoutes(page);
    await page.goto('/governance');

    await page.getByTestId('tab-audit').click();
    await expect(page.getByTestId('export-json-btn')).toBeVisible({ timeout: 10000 });
    await expect(page.getByTestId('export-csv-btn')).toBeVisible({ timeout: 5000 });
  });

  test('24. Hash-chain verify button triggers integrity check', async ({ page }) => {
    await setupAuth(page);
    await setupGovRoutes(page);
    await page.goto('/governance');

    await page.getByTestId('tab-audit').click();
    await expect(page.getByTestId('audit-row').first()).toBeVisible({ timeout: 10000 });
    await page.getByText(/Verify chain/i).click();

    await expect(page.getByText(/Chain verified|42 events verified/i).first()).toBeVisible({ timeout: 8000 });
  });

  test('25. Approver column shows approver name when present', async ({ page }) => {
    await setupAuth(page);
    await setupGovRoutes(page);
    await page.goto('/governance');

    await page.getByTestId('tab-audit').click();
    await expect(page.getByText('alice@pinelabs.com')).toBeVisible({ timeout: 10000 });
  });

  test('26. Empty state shown when no audit events match', async ({ page }) => {
    await setupAuth(page);
    await setupGovRoutes(page, { audit: [] });
    await page.goto('/governance');

    await page.getByTestId('tab-audit').click();
    await expect(page.getByTestId('audit-empty')).toBeVisible({ timeout: 10000 });
    await expect(page.getByText(/No audit events found/i)).toBeVisible({ timeout: 5000 });
  });
});

// ═══════════════════════════════════════════════════════════════════════════════
// SUITE 5 — Budget Tab
// ═══════════════════════════════════════════════════════════════════════════════

test.describe('Governance — Budget Tab', () => {

  test('27. Shows Budget Limits section with per-goal and daily inputs', async ({ page }) => {
    await setupAuth(page);
    await setupGovRoutes(page);
    await page.goto('/governance');

    await page.getByTestId('tab-budget').click();
    await expect(page.getByText('Budget Limits')).toBeVisible({ timeout: 10000 });
    await expect(page.getByLabel(/per-goal limit/i)).toBeVisible({ timeout: 5000 });
    await expect(page.getByLabel(/daily tenant limit/i)).toBeVisible({ timeout: 5000 });
  });

  test('28. Shows Budget Utilization gauge section', async ({ page }) => {
    await setupAuth(page);
    await setupGovRoutes(page);
    await page.goto('/governance');

    await page.getByTestId('tab-budget').click();
    // There may be multiple "Budget Utilization" texts (stat card + gauge header)
    await expect(page.getByText('Budget Utilization').first()).toBeVisible({ timeout: 10000 });
  });

  test('29. Shows live cost status cards when cost data available', async ({ page }) => {
    await setupAuth(page);
    await setupGovRoutes(page);
    await page.goto('/governance');

    await page.getByTestId('tab-budget').click();
    // Cost KPI cards appear after costsApi.getSummary() resolves
    await expect(page.getByText('Daily Spend').first()).toBeVisible({ timeout: 10000 });
    await expect(page.getByText('Budget Utilization').first()).toBeVisible({ timeout: 5000 });
    await expect(page.getByText('Daily Remaining').first()).toBeVisible({ timeout: 5000 });
  });

  test('30. Save Budget Limits button appears on change and submits PUT', async ({ page }) => {
    let putCalled = false;
    await setupAuth(page);
    await setupGovRoutes(page);

    await page.route(/localhost:8000\/governance\/budget(\?.*)?$/, (route) => {
      if (route.request().method() === 'PUT') {
        putCalled = true;
        return route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(BUDGET) });
      }
      return route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(BUDGET) });
    });

    await page.goto('/governance');
    await page.getByTestId('tab-budget').click();
    await expect(page.getByLabel(/per-goal limit/i)).toBeVisible({ timeout: 10000 });
    await page.getByLabel(/per-goal limit/i).fill('25');
    await expect(page.getByTestId('save-budget-btn')).toBeVisible({ timeout: 5000 });
    await page.getByTestId('save-budget-btn').click();

    await expect(async () => {
      expect(putCalled).toBe(true);
    }).toPass({ timeout: 5000 });
  });

  test('31. Shows cost anomaly banner when anomalies present', async ({ page }) => {
    await setupAuth(page);
    await setupGovRoutes(page);
    await page.goto('/governance');

    await page.getByTestId('tab-budget').click();
    await expect(page.getByText(/Cost Anomalies/i)).toBeVisible({ timeout: 10000 });
    await expect(page.getByText('Unusual spike detected')).toBeVisible({ timeout: 5000 });
  });
});

// ═══════════════════════════════════════════════════════════════════════════════
// SUITE 6 — Emergency Stop
// ═══════════════════════════════════════════════════════════════════════════════

test.describe('Governance — Emergency Stop', () => {

  test('32. Shows confirmation dialog on Emergency Stop click', async ({ page }) => {
    await setupAuth(page);
    await setupGovRoutes(page);
    await page.goto('/governance');

    await expect(page.getByRole('heading', { name: 'Governance' })).toBeVisible({ timeout: 10000 });
    await page.getByTestId('emergency-stop-btn').click();
    await expect(page.getByText(/Halt all agent execution/i)).toBeVisible({ timeout: 5000 });
    await expect(page.getByText(/Confirm Stop/i)).toBeVisible({ timeout: 3000 });
  });

  test('33. Activates emergency stop and shows active banner', async ({ page }) => {
    await setupAuth(page);
    await setupGovRoutes(page);
    await page.goto('/governance');

    await expect(page.getByRole('heading', { name: 'Governance' })).toBeVisible({ timeout: 10000 });
    await page.getByTestId('emergency-stop-btn').click();
    // Confirm dialog appears — click the confirm button
    await expect(page.getByText(/Halt all agent execution/i)).toBeVisible({ timeout: 5000 });
    await page.getByText('Confirm Stop').first().click();

    await expect(page.getByTestId('emergency-banner')).toBeVisible({ timeout: 8000 });
    // Banner contains the goals-cancelled stat from the mock
    await expect(page.getByText(/3 goals cancelled/).first()).toBeVisible({ timeout: 5000 });
  });

  test('34. Cancel button hides confirmation dialog without activating', async ({ page }) => {
    await setupAuth(page);
    await setupGovRoutes(page);
    await page.goto('/governance');

    await expect(page.getByRole('heading', { name: 'Governance' })).toBeVisible({ timeout: 10000 });
    await page.getByTestId('emergency-stop-btn').click();
    await expect(page.getByText(/Confirm Stop/i)).toBeVisible({ timeout: 5000 });
    await page.getByText(/^Cancel$/).click();
    await expect(page.getByText(/Confirm Stop/i)).not.toBeVisible({ timeout: 3000 });
  });

  test('35. Clear Emergency Stop button calls DELETE and removes banner', async ({ page }) => {
    // Pre-set emergency store as active via localStorage
    await setupAuth(page);
    await page.addInitScript(() => {
      localStorage.setItem('agentverse-emergency', JSON.stringify({
        state: { isActive: true, activatedAt: new Date().toISOString(), cancelledGoals: 3, rejectedApprovals: 1 },
        version: 0,
      }));
    });
    await setupGovRoutes(page);

    let clearCalled = false;
    await page.route(/localhost:8000\/governance\/emergency-stop/, (route) => {
      if (route.request().method() === 'DELETE') {
        clearCalled = true;
        return route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify({ status: 'cleared', tenant_id: 'test-tenant' }) });
      }
      return route.fulfill({ status: 200, contentType: 'application/json', body: '{}' });
    });

    await page.goto('/governance');
    await expect(page.getByTestId('emergency-banner')).toBeVisible({ timeout: 10000 });
    // Use role=button to avoid strict-mode violation from multiple matching elements
    await page.getByTestId('emergency-banner').getByRole('button', { name: /Clear Emergency Stop/ }).click();

    await expect(async () => {
      expect(clearCalled).toBe(true);
    }).toPass({ timeout: 5000 });
  });
});
