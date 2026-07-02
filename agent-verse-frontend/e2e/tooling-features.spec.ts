/**
 * E2E tests — Artifacts Browser, RPA Live, A2A, Simulation, Budget Manager
 */
import { test, expect, type Page } from '@playwright/test';

async function setupAuth(page: Page) {
  await page.route(/localhost:8000/, (route) =>
    route.fulfill({ status: 404, contentType: 'application/json', body: '{"detail":"not found"}' })
  );
  await page.addInitScript(() => {
    localStorage.setItem('av-auth', JSON.stringify({
      state: { apiKey: 'test-key', tenantId: 'test-tenant', plan: 'free', isAuthenticated: true },
      version: 0,
    }));
    localStorage.setItem('av_api_key', 'test-key');
    sessionStorage.setItem('av_api_key', 'test-key');
  });
  await page.route('**/tenants/me', (route) =>
    route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify({ tenant_id: 'test-tenant', name: 'Test', plan: 'free' }) })
  );
}

const ARTIFACT = {
  id: 'art-001', name: 'report.json', artifact_type: 'report',
  storage_uri: 'https://example.com/report.json',
  content_type: 'application/json', size_bytes: 1024,
  goal_id: 'goal-abc123', created_at: new Date().toISOString(),
};

// ═══════════════════════════════════════════════════════════════════════════
// ARTIFACTS
// ═══════════════════════════════════════════════════════════════════════════

test.describe('Artifacts Browser', () => {
  test('shows heading and empty state', async ({ page }) => {
    await setupAuth(page);
    await page.route(/localhost:8000\/artifacts/, (route) =>
      route.fulfill({ status: 200, contentType: 'application/json', body: '[]' })
    );
    await page.goto('/artifacts');
    await expect(page.getByRole('heading', { name: /artifacts/i })).toBeVisible({ timeout: 15000 });
    await expect(page.getByText(/no artifacts yet/i)).toBeVisible();
  });

  test('displays artifact card with name and type', async ({ page }) => {
    await setupAuth(page);
    await page.route(/localhost:8000\/artifacts/, (route) =>
      route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify([ARTIFACT]) })
    );
    await page.goto('/artifacts');
    await expect(page.getByText('report.json')).toBeVisible({ timeout: 15000 });
    await expect(page.getByText('report')).toBeVisible();
  });

  test('search filters artifacts', async ({ page }) => {
    await setupAuth(page);
    await page.route(/localhost:8000\/artifacts/, (route) =>
      route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify([
        ARTIFACT,
        { ...ARTIFACT, id: 'art-002', name: 'screenshot.png', artifact_type: 'screenshot' },
      ]) })
    );
    await page.goto('/artifacts');
    await expect(page.getByText('report.json')).toBeVisible({ timeout: 15000 });
    await page.getByLabel(/search artifacts/i).fill('screenshot');
    await expect(page.getByText('report.json')).not.toBeVisible();
    await expect(page.getByText('screenshot.png')).toBeVisible();
  });

  test('type filter pills render', async ({ page }) => {
    await setupAuth(page);
    await page.route(/localhost:8000\/artifacts/, (route) =>
      route.fulfill({ status: 200, contentType: 'application/json', body: '[]' })
    );
    await page.goto('/artifacts');
    await expect(page.getByRole('button', { name: /^all$/i })).toBeVisible({ timeout: 10000 });
    await expect(page.getByRole('button', { name: /^image$/i })).toBeVisible();
    await expect(page.getByRole('button', { name: /^screenshot$/i })).toBeVisible();
  });

  test('opening artifact card shows detail drawer', async ({ page }) => {
    await setupAuth(page);
    await page.route(/localhost:8000\/artifacts/, (route) =>
      route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify([ARTIFACT]) })
    );
    await page.goto('/artifacts');
    await page.getByTestId('artifact-card').click();
    await expect(page.getByRole('button', { name: /^delete$/i })).toBeVisible({ timeout: 5000 });
    await expect(page.getByRole('button', { name: /use as input/i })).toBeVisible();
    await expect(page.getByRole('button', { name: /go to goal/i })).toBeVisible();
  });

  test('sort dropdown works', async ({ page }) => {
    await setupAuth(page);
    await page.route(/localhost:8000\/artifacts/, (route) =>
      route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify([ARTIFACT]) })
    );
    await page.goto('/artifacts');
    await expect(page.getByTestId('artifact-card')).toBeVisible({ timeout: 10000 });
    const sortSelect = page.getByLabel(/sort artifacts/i);
    await expect(sortSelect).toBeVisible();
    await sortSelect.selectOption('oldest');
    await expect(page.getByText('Oldest first')).toBeVisible();
  });
});

// ═══════════════════════════════════════════════════════════════════════════
// RPA
// ═══════════════════════════════════════════════════════════════════════════

test.describe('RPA Live', () => {
  test('shows heading and New Session button', async ({ page }) => {
    await setupAuth(page);
    await page.route(/localhost:8000\/rpa\/sessions/, (route) => {
      if (route.request().method() === 'GET')
        return route.fulfill({ status: 200, contentType: 'application/json', body: '[]' });
      return route.fulfill({ status: 201, contentType: 'application/json', body: JSON.stringify({ session_id: 'new', status: 'active', created_at: new Date().toISOString() }) });
    });
    await page.goto('/rpa/live');
    await expect(page.getByRole('heading', { name: /rpa live/i })).toBeVisible({ timeout: 15000 });
    await expect(page.getByRole('button', { name: /new session/i })).toBeVisible();
  });

  test('shows empty session list message', async ({ page }) => {
    await setupAuth(page);
    await page.route(/localhost:8000\/rpa\/sessions/, (route) =>
      route.fulfill({ status: 200, contentType: 'application/json', body: '[]' })
    );
    await page.goto('/rpa/live');
    await expect(page.getByText(/no active sessions/i)).toBeVisible({ timeout: 15000 });
  });

  test('shows session in list with status badge', async ({ page }) => {
    await setupAuth(page);
    await page.route(/localhost:8000\/rpa\/sessions(\?.*)?$/, (route) =>
      route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify([{ session_id: 'sess-abc123', status: 'active', created_at: new Date().toISOString() }]) })
    );
    await page.goto('/rpa/live');
    await expect(page.getByText(/sess-abc1/)).toBeVisible({ timeout: 15000 });
    await expect(page.getByText('active')).toBeVisible();
  });

  test('selecting session shows empty viewport message', async ({ page }) => {
    await setupAuth(page);
    await page.route(/localhost:8000\/rpa\/sessions(\?.*)?$/, (route) =>
      route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify([{ session_id: 'sess-abc123', status: 'active', created_at: new Date().toISOString() }]) })
    );
    await page.route(/localhost:8000\/rpa\/sessions\/.*\/screenshot/, (route) =>
      route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify({ session_id: 'sess-abc123', screenshot_data_uri: '', url: '', timestamp: '' }) })
    );
    await page.goto('/rpa/live');
    await expect(page.getByText(/sess-abc1/)).toBeVisible({ timeout: 10000 });
    await page.getByText(/sess-abc1/).click();
    // After clicking, the takeover button should appear
    await expect(page.getByRole('button', { name: /takeover/i })).toBeVisible({ timeout: 5000 });
  });
});

// ═══════════════════════════════════════════════════════════════════════════
// A2A
// ═══════════════════════════════════════════════════════════════════════════

test.describe('A2A Network', () => {
  const CARD = { agent_id: 'av', name: 'AgentVerse', version: '2.0', description: 'desc', endpoint: 'http://localhost:8000', authentication: { scheme: 'hmac-sha256', header: 'X-A2A', note: '' }, capabilities: ['goal_execution'], supported_task_types: ['goal'] };

  test('shows heading and three tabs', async ({ page }) => {
    await setupAuth(page);
    await page.route(/localhost:8000\/.well-known\/agent\.json/, (route) =>
      route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(CARD) })
    );
    await page.route(/localhost:8000\/a2a\/tasks/, (route) =>
      route.fulfill({ status: 200, contentType: 'application/json', body: '[]' })
    );
    await page.goto('/a2a');
    await expect(page.getByRole('heading', { name: /a2a network/i })).toBeVisible({ timeout: 15000 });
    await expect(page.getByRole('tab', { name: /tasks/i })).toBeVisible();
    await expect(page.getByRole('tab', { name: /agent card/i })).toBeVisible();
    await expect(page.getByRole('tab', { name: /remote agents/i })).toBeVisible();
  });

  test('Tasks tab shows dispatch form', async ({ page }) => {
    await setupAuth(page);
    await page.route(/localhost:8000\/.well-known\/agent\.json/, (route) =>
      route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(CARD) })
    );
    await page.route(/localhost:8000\/a2a\/tasks/, (route) =>
      route.fulfill({ status: 200, contentType: 'application/json', body: '[]' })
    );
    await page.goto('/a2a');
    await expect(page.getByText(/dispatch task/i)).toBeVisible({ timeout: 10000 });
    await expect(page.getByLabel(/goal/i)).toBeVisible();
  });

  test('dispatches task and shows success', async ({ page }) => {
    await setupAuth(page);
    await page.route(/localhost:8000\/.well-known\/agent\.json/, (route) =>
      route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(CARD) })
    );
    await page.route(/localhost:8000\/a2a\/tasks/, async (route) => {
      if (route.request().method() === 'POST')
        return route.fulfill({ status: 202, contentType: 'application/json', body: JSON.stringify({ task_id: 'new-task-123', status: 'accepted', message: 'Accepted' }) });
      return route.fulfill({ status: 200, contentType: 'application/json', body: '[]' });
    });
    await page.goto('/a2a');
    await page.getByLabel(/goal/i).fill('Test A2A goal');
    await page.getByRole('button', { name: /dispatch task/i }).click();
    await expect(page.getByText(/task dispatched/i)).toBeVisible({ timeout: 10000 });
    await expect(page.getByText(/new-task-123/)).toBeVisible();
  });

  test('Agent Card tab shows card data', async ({ page }) => {
    await setupAuth(page);
    await page.route(/localhost:8000\/.well-known\/agent\.json/, (route) =>
      route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(CARD) })
    );
    await page.route(/localhost:8000\/a2a\/tasks/, (route) =>
      route.fulfill({ status: 200, contentType: 'application/json', body: '[]' })
    );
    await page.goto('/a2a');
    await page.getByRole('tab', { name: /agent card/i }).click();
    await expect(page.getByText('AgentVerse')).toBeVisible({ timeout: 10000 });
    await expect(page.getByText(/v2\.0/)).toBeVisible();
    await expect(page.getByText(/hmac-sha256/)).toBeVisible();
  });

  test('Remote Agents tab shows empty state', async ({ page }) => {
    await setupAuth(page);
    await page.route(/localhost:8000\/.well-known\/agent\.json/, (route) =>
      route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(CARD) })
    );
    await page.route(/localhost:8000\/a2a\/tasks/, (route) =>
      route.fulfill({ status: 200, contentType: 'application/json', body: '[]' })
    );
    await page.goto('/a2a');
    await page.getByRole('tab', { name: /remote agents/i }).click();
    await expect(page.getByText(/no remote agents registered/i)).toBeVisible({ timeout: 5000 });
    await expect(page.getByRole('button', { name: /register agent/i })).toBeVisible();
  });
});

// ═══════════════════════════════════════════════════════════════════════════
// SIMULATION
// ═══════════════════════════════════════════════════════════════════════════

test.describe('Simulation Studio', () => {
  test('shows heading and goal textarea', async ({ page }) => {
    await setupAuth(page);
    await page.route(/localhost:8000\/enterprise\/simulation\/available-tools/, (route) =>
      route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify({ tools: [], total: 0 }) })
    );
    await page.route(/localhost:8000\/governance\/simulate/, (route) =>
      route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify({ summary: { allowed_tools: [], denied_tools: [], requires_approval: [], would_block_execution: false, hitl_approvals_needed: 0 }, policy_checks: [] }) })
    );
    await page.goto('/simulation');
    await expect(page.getByRole('heading', { name: /simulation studio/i })).toBeVisible({ timeout: 15000 });
    await expect(page.getByLabel(/simulation goal/i)).toBeVisible();
  });

  test('Run Simulation button disabled without goal', async ({ page }) => {
    await setupAuth(page);
    await page.route(/localhost:8000\/enterprise\/simulation/, (route) =>
      route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify({ tools: [], total: 0 }) })
    );
    await page.goto('/simulation');
    await expect(page.getByRole('button', { name: /run simulation/i })).toBeDisabled({ timeout: 10000 });
  });

  test('Mock Tools section expands', async ({ page }) => {
    await setupAuth(page);
    await page.route(/localhost:8000\/enterprise\/simulation\/available-tools/, (route) =>
      route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify({ tools: [{ name: 'jira_search', description: 'Search', server_id: 'jira' }], total: 1 }) })
    );
    await page.goto('/simulation');
    await page.getByRole('button', { name: /mock tools/i }).click();
    await expect(page.getByPlaceholder(/search available tools/i)).toBeVisible({ timeout: 5000 });
  });

  test('shows governance preview after typing goal', async ({ page }) => {
    await setupAuth(page);
    await page.route(/localhost:8000\/governance\/simulate/, (route) =>
      route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify({ summary: { allowed_tools: ['tool1'], denied_tools: [], requires_approval: [], would_block_execution: false, hitl_approvals_needed: 0 }, policy_checks: [] }) })
    );
    await page.route(/localhost:8000\/enterprise\/simulation\/available-tools/, (route) =>
      route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify({ tools: [], total: 0 }) })
    );
    await page.goto('/simulation');
    await page.getByLabel(/simulation goal/i).fill('Deploy the app');
    await expect(page.getByText(/policy preview/i)).toBeVisible({ timeout: 3000 });
    await expect(page.getByText(/execution allowed/i)).toBeVisible({ timeout: 5000 });
  });
});

// ═══════════════════════════════════════════════════════════════════════════
// BUDGET MANAGER
// ═══════════════════════════════════════════════════════════════════════════

test.describe('Budget Manager', () => {
  const SUMMARY = {
    total_usd: 12.34, period_days: 30,
    budget_status: { daily_spent: 2.50, daily_limit: 50.0, daily_remaining: 47.50, budget_pct_remaining: 95, goal_spent: 0.5 },
    cost_by_day: [{ date: '2026-06-01', cost_usd: 1.0 }, { date: '2026-06-02', cost_usd: 1.5 }],
    cost_by_model: { 'gpt-4': 8.0, 'claude-3': 4.34 },
    by_agent: [],
  };

  test('shows heading', async ({ page }) => {
    await setupAuth(page);
    await page.route(/localhost:8000\/costs\/summary/, (route) =>
      route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(SUMMARY) })
    );
    await page.route(/localhost:8000\/costs\/per-agent/, (route) =>
      route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify({ agents: [] }) })
    );
    await page.route(/localhost:8000\/costs\/budgets/, (route) =>
      route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify({ per_goal_usd: 10, per_tenant_daily_usd: 50, daily_spent: 2.5, daily_limit: 50 }) })
    );
    await page.route(/localhost:8000\/costs\/anomalies/, (route) =>
      route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify({ anomalies: [] }) })
    );
    await page.route(/localhost:8000\/governance\/budget/, (route) =>
      route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify({ tenant_id: 't', per_goal_usd: 10, per_tenant_daily_usd: 50 }) })
    );
    await page.goto('/settings/budgets');
    await expect(page.getByRole('heading', { name: /budget manager/i })).toBeVisible({ timeout: 15000 });
  });

  test('shows daily spend stat card', async ({ page }) => {
    await setupAuth(page);
    await page.route(/localhost:8000\/costs\/summary/, (route) =>
      route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(SUMMARY) })
    );
    await page.route(/localhost:8000\/costs\/per-agent/, (route) =>
      route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify({ agents: [] }) })
    );
    await page.route(/localhost:8000\/costs\/budgets/, (route) =>
      route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify({ daily_spent: 2.5, daily_limit: 50, per_goal_usd: 10, per_tenant_daily_usd: 50 }) })
    );
    await page.route(/localhost:8000\/costs\/anomalies/, (route) =>
      route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify({ anomalies: [] }) })
    );
    await page.route(/localhost:8000\/governance\/budget/, (route) =>
      route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify({ tenant_id: 't', per_goal_usd: 10, per_tenant_daily_usd: 50 }) })
    );
    await page.goto('/settings/budgets');
    await expect(page.getByText(/daily spend/i)).toBeVisible({ timeout: 15000 });
    await expect(page.getByText(/\$2\.50/)).toBeVisible();
  });

  test('shows no anomalies green state', async ({ page }) => {
    await setupAuth(page);
    await page.route(/localhost:8000\/costs\/summary/, (route) =>
      route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(SUMMARY) })
    );
    await page.route(/localhost:8000\/costs\/per-agent/, (route) =>
      route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify({ agents: [] }) })
    );
    await page.route(/localhost:8000\/costs\/budgets/, (route) =>
      route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify({ daily_spent: 0, daily_limit: 50, per_goal_usd: 10, per_tenant_daily_usd: 50 }) })
    );
    await page.route(/localhost:8000\/costs\/anomalies/, (route) =>
      route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify({ anomalies: [] }) })
    );
    await page.route(/localhost:8000\/governance\/budget/, (route) =>
      route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify({ tenant_id: 't', per_goal_usd: 10, per_tenant_daily_usd: 50 }) })
    );
    await page.goto('/settings/budgets');
    await expect(page.getByText(/no anomalies detected/i)).toBeVisible({ timeout: 15000 });
  });

  test('Cost Predictor section expands', async ({ page }) => {
    await setupAuth(page);
    await page.route(/localhost:8000\/costs\/summary/, (route) =>
      route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(SUMMARY) })
    );
    await page.route(/localhost:8000\/costs\/per-agent/, (route) =>
      route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify({ agents: [] }) })
    );
    await page.route(/localhost:8000\/costs\/budgets/, (route) =>
      route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify({ daily_spent: 0, daily_limit: 50, per_goal_usd: 10, per_tenant_daily_usd: 50 }) })
    );
    await page.route(/localhost:8000\/costs\/anomalies/, (route) =>
      route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify({ anomalies: [] }) })
    );
    await page.route(/localhost:8000\/governance\/budget/, (route) =>
      route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify({ tenant_id: 't', per_goal_usd: 10, per_tenant_daily_usd: 50 }) })
    );
    await page.goto('/settings/budgets');
    await expect(page.getByText(/cost predictor/i)).toBeVisible({ timeout: 15000 });
    await page.getByText(/cost predictor/i).click();
    await expect(page.getByPlaceholder(/estimate goal cost/i)).toBeVisible({ timeout: 3000 });
  });
});
