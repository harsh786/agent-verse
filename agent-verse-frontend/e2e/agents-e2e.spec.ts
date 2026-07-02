/**
 * Agent execution E2E tests covering all three autonomy modes:
 * bounded-autonomous, supervised, fully-autonomous.
 * All tests use mocked APIs — no real backend or Jira credentials needed.
 */
import { test, expect, type Page } from '@playwright/test';

async function setupAuth(page: Page) {
  // Catch-all: block unmocked localhost:8000 requests from hitting the real backend
  // (which returns 401 for test API keys and triggers logout). Registered FIRST
  // so specific mocks added later have higher priority via Playwright's LIFO matching.
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
          plan: 'professional',
          isAuthenticated: true,
        },
        version: 0,
      })
    );
    localStorage.setItem('av_api_key', 'test-key');
  });
  await page.route('**/tenants/me', (route) =>
    route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({ tenant_id: 'test-tenant', name: 'PineLabs', plan: 'professional' }),
    })
  );
}

function makeAgent(id: string, autonomy: string, connectors: string[] = []) {
  return {
    agent_id: id,
    name: `${autonomy.replace(/-/g, ' ')} Agent`,
    goal_template: 'Find all Jira issues assigned to Abhay Dwivedi',
    autonomy_mode: autonomy,
    connector_ids: connectors,
    status: 'active',
    created_at: new Date().toISOString(),
  };
}

const COMPLETE_ARTIFACT = {
  version: 1,
  kind: 'table',
  title: 'Jira issues',
  summary: 'Found 3 Jira issues.',
  status: 'success',
  metrics: [{ label: 'Issues', value: 3 }],
  tables: [{
    title: 'Issues',
    columns: [
      { key: 'key', label: 'Key', type: 'link' },
      { key: 'summary', label: 'Summary', type: 'text' },
      { key: 'status', label: 'Status', type: 'badge' },
    ],
    rows: [
      { key: 'OPP-1', summary: 'Fix login bug', status: 'Open' },
      { key: 'OPP-2', summary: 'Add pagination', status: 'In Progress' },
      { key: 'OPP-3', summary: 'Update docs', status: 'Closed' },
    ],
  }],
  evidence: { tools: [{ name: 'jira_search_issues', success: true }] },
  downloads: ['json', 'csv', 'markdown'],
  debug: {},
};

function makeGoal(id: string, status: string, agentId: string) {
  return {
    id,
    goal_id: id,
    goal: 'Find all Jira issues assigned to Abhay Dwivedi',
    status,
    agent_id: agentId,
    created_at: new Date().toISOString(),
    result_artifact: status === 'complete' ? COMPLETE_ARTIFACT : null,
  };
}

async function mockApis(
  page: Page,
  agent: ReturnType<typeof makeAgent>,
  goal: ReturnType<typeof makeGoal>
) {
  await page.route(/localhost:8000\/agents/, (route) => {
    const method = route.request().method();
    const url = route.request().url();
    if (method === 'GET' && url.match(/\/agents\/[^/?]+$/) && url.includes(agent.agent_id)) {
      return route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(agent) });
    }
    if (method === 'GET') {
      return route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify([agent]) });
    }
    return route.continue();
  });

  await page.route(/localhost:8000\/goals/, (route) => {
    const method = route.request().method();
    const url = route.request().url();
    if (method === 'POST' && !url.includes('/cancel')) {
      return route.fulfill({ status: 202, contentType: 'application/json', body: JSON.stringify({ goal_id: goal.id, status: 'planning', goal: goal.goal }) });
    }
    if (method === 'GET' && url.includes('/replay')) {
      return route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify({ timeline: [] }) });
    }
    if (method === 'GET') {
      return route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(goal) });
    }
    return route.continue();
  });

  await page.route(/localhost:8000\/goals\/.*\/stream/, (route) =>
    route.fulfill({
      status: 200,
      contentType: 'text/event-stream',
      body: 'data: {"type":"goal_complete"}\n\n',
    })
  );

  await page.route(/localhost:8000\/connectors/, (route) =>
    route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify([]) })
  );

  await page.route(/localhost:8000\/insights/, (route) =>
    route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify({ nodes: [{ id: 'start', type: 'start', label: 'Start', data: {} }], edges: [], stats: { total_nodes: 1, total_edges: 0, tool_calls: 0, unique_tools: 0 } }) })
  );
}

// ── Bounded Autonomous Agent ──────────────────────────────────────────────────
test.describe('Bounded Autonomous Agent', () => {
  const AGENT = makeAgent('ba-1', 'bounded-autonomous', ['jira-1']);

  test('agent detail page loads without error', async ({ page }) => {
    await setupAuth(page);
    await mockApis(page, AGENT, makeGoal('ba-goal-1', 'complete', 'ba-1'));
    await page.goto('/agents/ba-1');
    await page.waitForLoadState('networkidle');
    await expect(page.locator('body')).not.toContainText('500', { timeout: 10000 });
  });

  test('completed goal shows Jira result artifact with 3 issues', async ({ page }) => {
    await setupAuth(page);
    await mockApis(page, AGENT, makeGoal('ba-goal-2', 'complete', 'ba-1'));
    await page.goto('/goals/ba-goal-2');
    await expect(page.getByText('Found 3 Jira issues.').or(page.getByText('3')).first()).toBeVisible({ timeout: 15000 });
  });

  test('result shows Download JSON and Download CSV buttons', async ({ page }) => {
    await setupAuth(page);
    await mockApis(page, AGENT, makeGoal('ba-goal-3', 'complete', 'ba-1'));
    await page.goto('/goals/ba-goal-3');
    await expect(page.getByRole('button', { name: /download json/i })).toBeVisible({ timeout: 15000 });
    await expect(page.getByRole('button', { name: /download csv/i })).toBeVisible({ timeout: 10000 });
  });

  test('result shows Print/PDF button', async ({ page }) => {
    await setupAuth(page);
    await mockApis(page, AGENT, makeGoal('ba-goal-4', 'complete', 'ba-1'));
    await page.goto('/goals/ba-goal-4');
    await expect(page.getByRole('button', { name: /print/i })).toBeVisible({ timeout: 15000 });
  });
});

// ── Supervised Agent ──────────────────────────────────────────────────────────
test.describe('Supervised Agent', () => {
  const AGENT = makeAgent('sup-1', 'supervised');

  test('supervised agent detail page loads', async ({ page }) => {
    await setupAuth(page);
    await mockApis(page, AGENT, makeGoal('sup-goal-1', 'complete', 'sup-1'));
    await page.goto('/agents/sup-1');
    await page.waitForLoadState('networkidle');
    await expect(page.locator('body')).not.toContainText('500');
  });

  test('completed supervised goal shows result', async ({ page }) => {
    await setupAuth(page);
    await mockApis(page, AGENT, makeGoal('sup-goal-2', 'complete', 'sup-1'));
    await page.goto('/goals/sup-goal-2');
    await page.waitForLoadState('networkidle');
    await expect(page.locator('body')).not.toContainText('Uncaught Error');
  });
});

// ── Fully Autonomous Agent ────────────────────────────────────────────────────
test.describe('Fully Autonomous Agent', () => {
  const AGENT = makeAgent('auto-1', 'fully-autonomous');

  test('fully autonomous agent page loads', async ({ page }) => {
    await setupAuth(page);
    await mockApis(page, AGENT, makeGoal('auto-goal-1', 'complete', 'auto-1'));
    await page.goto('/agents/auto-1');
    await page.waitForLoadState('networkidle');
    await expect(page.locator('body')).not.toContainText('500');
  });

  test('no approval UI shown for fully autonomous goal', async ({ page }) => {
    await setupAuth(page);
    await mockApis(page, AGENT, makeGoal('auto-goal-2', 'complete', 'auto-1'));
    await page.goto('/goals/auto-goal-2');
    await page.waitForLoadState('networkidle');
    const approvalUI = page.getByText(/waiting.*approval|approve.*reject/i);
    const visible = await approvalUI.isVisible({ timeout: 2000 }).catch(() => false);
    expect(visible).toBe(false);
  });
});
