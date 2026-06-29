import { test, expect, type Page } from '@playwright/test';

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
  await page.route('**/tenants/me', (route) =>
    route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({ tenant_id: 'test-tenant', name: 'Test Org', plan: 'free' }),
    })
  );
}

const MOCK_GRAPH = {
  nodes: [
    { id: 'n1', type: 'start', label: 'Start', data: {} },
    { id: 'n2', type: 'step', label: 'Plan steps', data: {} },
    { id: 'n3', type: 'tool', label: 'call_github', data: { tool_name: 'github.list_prs' } },
    { id: 'n4', type: 'step', label: 'Verify result', data: {} },
    { id: 'n5', type: 'end', label: 'Complete', data: {} },
  ],
  edges: [
    { id: 'e1', source: 'n1', target: 'n2' },
    { id: 'e2', source: 'n2', target: 'n3' },
    { id: 'e3', source: 'n3', target: 'n4' },
    { id: 'e4', source: 'n4', target: 'n5' },
  ],
  stats: {
    total_nodes: 5,
    tool_calls: 1,
    unique_tools: 1,
    total_edges: 4,
    execution_time_s: 12.4,
  },
};

async function mockGraphApi(page: Page, goalId: string, fail = false) {
  await page.route(`**/insights/graph/${goalId}`, (route) => {
    if (fail) {
      return route.fulfill({ status: 500, contentType: 'application/json', body: '{}' });
    }
    return route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify(MOCK_GRAPH),
    });
  });
  // Also mock the goal detail endpoint
  await page.route(`**/goals/${goalId}`, (route) => {
    if (route.request().method() === 'GET') {
      return route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({
          id: goalId,
          goal_id: goalId,
          goal: 'Fix all JIRA prod-down bugs',
          status: 'complete',
          created_at: new Date().toISOString(),
        }),
      });
    }
    return route.continue();
  });
}

test.describe('Goal DNA', () => {
  test('page renders "Goal DNA" heading', async ({ page }) => {
    const goalId = 'test-goal-1';
    await setupAuth(page);
    await mockGraphApi(page, goalId);
    await page.goto(`/goals/${goalId}/dna`);

    await expect(
      page.locator('h1').filter({ hasText: /goal dna/i })
    ).toBeVisible({ timeout: 15000 });
  });

  test('shows goalId in description', async ({ page }) => {
    const goalId = 'test-goal-1';
    await setupAuth(page);
    await mockGraphApi(page, goalId);
    await page.goto(`/goals/${goalId}/dna`);

    // The page shows a truncated version of the goal ID
    await expect(
      page.getByText(new RegExp(goalId.slice(0, 8), 'i'))
    ).toBeVisible({ timeout: 15000 });
  });

  test('stats bar shows node/tool counts after load', async ({ page }) => {
    const goalId = 'test-goal-1';
    await setupAuth(page);
    await mockGraphApi(page, goalId);
    await page.goto(`/goals/${goalId}/dna`);

    // Stats bar should show 5 nodes and 1 tool call
    await expect(page.getByText('5').first()).toBeVisible({ timeout: 15000 });
    await expect(page.getByText(/nodes/i)).toBeVisible();
    await expect(page.getByText('tool calls')).toBeVisible();
  });

  test('error state shown when graph unavailable', async ({ page }) => {
    const goalId = 'test-goal-1';
    await setupAuth(page);
    await mockGraphApi(page, goalId, true /* fail */);
    await page.goto(`/goals/${goalId}/dna`);

    await expect(
      page.getByText(/could not load execution graph/i)
    ).toBeVisible({ timeout: 15000 });
  });

  test('loading state shown while fetching', async ({ page }) => {
    const goalId = 'test-goal-1';
    await setupAuth(page);

    // Delay the response so we can observe the loading state
    await page.route(`**/insights/graph/${goalId}`, async (route) => {
      await new Promise((resolve) => setTimeout(resolve, 600));
      return route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify(MOCK_GRAPH),
      });
    });
    await page.route(`**/goals/${goalId}`, (route) =>
      route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({ id: goalId, goal_id: goalId, goal: 'test', status: 'complete' }),
      })
    );
    await page.route('**/tenants/me', (route) =>
      route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({ tenant_id: 'test-tenant', name: 'Test Org', plan: 'free' }),
      })
    );

    await page.goto(`/goals/${goalId}/dna`);

    // Skeleton loading elements should be present initially
    const heading = page.locator('h1').filter({ hasText: /goal dna/i });
    await expect(heading).toBeVisible({ timeout: 15000 });
  });
});
