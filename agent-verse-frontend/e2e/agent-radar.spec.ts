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

const MOCK_AGENT = {
  agent_id: 'test-agent-1',
  name: 'Prod Bot',
  description: 'Production automation agent',
  autonomy_mode: 'bounded-autonomous',
  status: 'active',
  created_at: new Date().toISOString(),
};

const MOCK_HEALTH = {
  agent_id: 'test-agent-1',
  health: {
    speed: 0.82,
    accuracy: 0.91,
    cost_efficiency: 0.67,
    tool_coverage: 0.75,
    success_rate: 0.88,
    coherence: 0.79,
  },
  sample_size: 34,
  updated_at: new Date().toISOString(),
};

const MOCK_BENCHMARKS = {
  platform_avg_success_rate: 0.74,
  platform_avg_accuracy: 0.80,
  platform_avg_speed: 0.70,
  updated_at: new Date().toISOString(),
};

async function mockApis(page: Page, agentId: string) {
  await page.route(`**/agents/${agentId}`, (route) =>
    route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify(MOCK_AGENT),
    })
  );
  await page.route(`**/insights/agent-health/${agentId}`, (route) =>
    route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify(MOCK_HEALTH),
    })
  );
  await page.route('**/insights/benchmarks', (route) =>
    route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify(MOCK_BENCHMARKS),
    })
  );
}

test.describe('Agent Radar', () => {
  test('page renders "Agent Health Radar" heading', async ({ page }) => {
    const agentId = 'test-agent-1';
    await setupAuth(page);
    await mockApis(page, agentId);
    await page.goto(`/agents/${agentId}/radar`);

    await expect(
      page.locator('h1').filter({ hasText: /agent health radar/i })
    ).toBeVisible({ timeout: 15000 });
  });

  test('platform comparison banner is shown (above/below average)', async ({ page }) => {
    const agentId = 'test-agent-1';
    await setupAuth(page);
    await mockApis(page, agentId);
    await page.goto(`/agents/${agentId}/radar`);

    // Success rate 88% vs platform avg 74% → should show "Above platform average"
    await expect(
      page.getByText(/above platform average/i).or(page.getByText(/below platform average/i))
    ).toBeVisible({ timeout: 15000 });
  });

  test('shows "above platform average" when success rate exceeds benchmark', async ({ page }) => {
    const agentId = 'test-agent-1';
    await setupAuth(page);
    await mockApis(page, agentId);
    await page.goto(`/agents/${agentId}/radar`);

    // MOCK_HEALTH.health.success_rate (0.88) > MOCK_BENCHMARKS.platform_avg_success_rate (0.74)
    await expect(page.getByText(/above platform average/i)).toBeVisible({ timeout: 15000 });
  });

  test('6 dimension cards visible', async ({ page }) => {
    const agentId = 'test-agent-1';
    await setupAuth(page);
    await mockApis(page, agentId);
    await page.goto(`/agents/${agentId}/radar`);

    // Wait for health data to load
    await expect(page.getByText(/above platform average/i)).toBeVisible({ timeout: 15000 });

    // There should be 6 progress bars (one per dimension)
    const progressBars = page.locator('[role="progressbar"]');
    await expect(progressBars).toHaveCount(6);
  });

  test('all dimension labels are present', async ({ page }) => {
    const agentId = 'test-agent-1';
    await setupAuth(page);
    await mockApis(page, agentId);
    await page.goto(`/agents/${agentId}/radar`);

    await expect(page.getByText(/above platform average/i)).toBeVisible({ timeout: 15000 });

    await expect(page.getByText('Speed').first()).toBeVisible();
    await expect(page.getByText('Accuracy').first()).toBeVisible();
    await expect(page.getByText('Cost Eff.').first()).toBeVisible();
    await expect(page.getByText('Tool Coverage').first()).toBeVisible();
    await expect(page.getByText('Success Rate').first()).toBeVisible();
    await expect(page.getByText('Coherence').first()).toBeVisible();
  });
});
