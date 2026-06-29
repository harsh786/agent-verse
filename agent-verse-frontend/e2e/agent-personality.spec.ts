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
  max_iterations: 12,
  model_override: 'claude-sonnet-4-5',
  status: 'active',
  created_at: new Date().toISOString(),
};

async function mockAgentApi(page: Page, agentId: string) {
  await page.route(`**/agents/${agentId}`, async (route) => {
    const method = route.request().method();
    if (method === 'GET') {
      return route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify(MOCK_AGENT),
      });
    }
    if (method === 'PATCH' || method === 'PUT') {
      return route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({ ...MOCK_AGENT, ...JSON.parse(route.request().postData() ?? '{}') }),
      });
    }
    return route.continue();
  });
}

test.describe('Agent Personality', () => {
  test('page renders "Agent Personality" heading', async ({ page }) => {
    const agentId = 'test-agent-1';
    await setupAuth(page);
    await mockAgentApi(page, agentId);
    await page.goto(`/agents/${agentId}/personality`);

    await expect(
      page.locator('h1').filter({ hasText: /agent personality/i })
    ).toBeVisible({ timeout: 15000 });
  });

  test('4 sliders are visible', async ({ page }) => {
    const agentId = 'test-agent-1';
    await setupAuth(page);
    await mockAgentApi(page, agentId);
    await page.goto(`/agents/${agentId}/personality`);

    await expect(
      page.locator('h1').filter({ hasText: /agent personality/i })
    ).toBeVisible({ timeout: 15000 });

    const sliders = page.locator('input[type="range"]');
    await expect(sliders).toHaveCount(4);
  });

  test('slider labels show left and right values', async ({ page }) => {
    const agentId = 'test-agent-1';
    await setupAuth(page);
    await mockAgentApi(page, agentId);
    await page.goto(`/agents/${agentId}/personality`);

    await expect(
      page.locator('h1').filter({ hasText: /agent personality/i })
    ).toBeVisible({ timeout: 15000 });

    // Autonomy slider: Supervised ↔ Fully Autonomous
    await expect(page.getByText('Supervised').first()).toBeVisible({ timeout: 15000 });
    await expect(page.getByText('Fully Autonomous').first()).toBeVisible({ timeout: 15000 });

    // Thoroughness slider: Fast ↔ Thorough
    await expect(page.getByText('Fast').first()).toBeVisible({ timeout: 15000 });
    await expect(page.getByText('Thorough').first()).toBeVisible({ timeout: 15000 });

    // Strategy slider: Deterministic ↔ Creative
    await expect(page.getByText('Deterministic').first()).toBeVisible({ timeout: 15000 });
    await expect(page.getByText('Creative').first()).toBeVisible({ timeout: 15000 });

    // Cost slider: Cost-Optimized ↔ Quality-First
    await expect(page.getByText('Cost-Optimized').first()).toBeVisible({ timeout: 15000 });
    await expect(page.getByText('Quality-First').first()).toBeVisible({ timeout: 15000 });
  });

  test('config preview section is visible', async ({ page }) => {
    const agentId = 'test-agent-1';
    await setupAuth(page);
    await mockAgentApi(page, agentId);
    await page.goto(`/agents/${agentId}/personality`);

    await expect(
      page.locator('h1').filter({ hasText: /agent personality/i })
    ).toBeVisible({ timeout: 15000 });

    // The generated config preview section
    await expect(page.getByText('Generated config')).toBeVisible({ timeout: 15000 });
    await expect(page.locator('p', { hasText: /^Mode$/ }).first()).toBeVisible({ timeout: 15000 });
    await expect(page.getByText('Max iterations')).toBeVisible({ timeout: 15000 });
  });

  test('Save Personality button is present', async ({ page }) => {
    const agentId = 'test-agent-1';
    await setupAuth(page);
    await mockAgentApi(page, agentId);
    await page.goto(`/agents/${agentId}/personality`);

    await expect(
      page.getByRole('button', { name: /save personality/i })
    ).toBeVisible({ timeout: 15000 });
  });
});
