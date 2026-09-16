/**
 * Model Control Center — E2E Tests
 *
 * Covers /models/control (ModelControlCenter) — an operational health/test
 * dashboard distinct from the /models registry-CRUD page. It used to share
 * the exact "models" route with ModelRegistryPage (making it unreachable
 * dead code); it now has its own route.
 *
 * Behavior under test:
 *  - Renders the model grid with provider/capability/quality/cost stats.
 *  - Renders the provider health strip from /models/health.
 *  - Provider filter buttons narrow the list.
 *  - "Test Connection" posts to /models/test and shows a result toast.
 */
import { test, expect, type Page } from '@playwright/test';
import { setupAuth } from './helpers/auth';

const MODELS = {
  models: [
    {
      provider: 'anthropic', model_id: 'claude-sonnet-5', display_name: 'Claude Sonnet 5',
      capabilities: ['text_generation', 'tool_use'], quality_score: 0.95,
      cost_per_1k_input: 0.003, context_window: 200000,
      health: { is_healthy: true },
    },
    {
      provider: 'openai', model_id: 'gpt-oss-20b', display_name: 'GPT OSS 20B',
      capabilities: ['text_generation'], quality_score: 0.8,
      cost_per_1k_input: 0, context_window: 32000,
      health: { is_healthy: false },
    },
  ],
};

const HEALTH = {
  providers: [
    { provider: 'anthropic', is_healthy: true, avg_latency_ms: 420 },
    { provider: 'openai', is_healthy: false, avg_latency_ms: 0 },
  ],
};

async function mockModelControlApi(page: Page): Promise<void> {
  await page.route(/localhost:8000\/models\/health/, (route) =>
    route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(HEALTH) })
  );
  await page.route(/localhost:8000\/models\/test/, (route) =>
    route.fulfill({
      status: 200, contentType: 'application/json',
      body: JSON.stringify({ status: 'ok', model: 'claude-sonnet-5', latency_ms: 380 }),
    })
  );
  await page.route(/localhost:8000\/models(\?.*)?$/, (route) =>
    route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(MODELS) })
  );
}

test.describe('Model Control Center', () => {
  test.beforeEach(async ({ page }) => {
    await setupAuth(page);
    await mockModelControlApi(page);
  });

  test('renders the model grid and provider health strip', async ({ page }) => {
    await page.goto('/models/control');
    await expect(page.getByRole('heading', { name: 'Model Control Center' })).toBeVisible();
    await expect(page.getByText('Claude Sonnet 5')).toBeVisible();
    await expect(page.getByText('GPT OSS 20B')).toBeVisible();
    await expect(page.getByText('2 models across 2 providers')).toBeVisible();
  });

  test('provider filter narrows the model list', async ({ page }) => {
    await page.goto('/models/control');
    await expect(page.getByText('Claude Sonnet 5')).toBeVisible();
    await page.getByRole('button', { name: 'anthropic', exact: true }).click();
    await expect(page.getByText('Claude Sonnet 5')).toBeVisible();
    await expect(page.getByText('GPT OSS 20B')).not.toBeVisible();
    await page.getByRole('button', { name: 'All Providers' }).click();
    await expect(page.getByText('GPT OSS 20B')).toBeVisible();
  });

  test('test connection posts to /models/test and shows a result', async ({ page }) => {
    await page.goto('/models/control');
    await page.getByText('Claude Sonnet 5').locator('..').locator('..')
      .getByRole('button', { name: /Test Connection/ }).click();
    await expect(page.getByText(/responded in 380ms/)).toBeVisible();
  });
});
