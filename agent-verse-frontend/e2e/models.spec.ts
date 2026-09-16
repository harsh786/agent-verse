/**
 * Model Registry — E2E Tests
 *
 * Covers /models (ModelRegistryPage). ModelControlCenter, which used to share
 * this same route as unreachable dead code, now has its own route at
 * /models/control — see models-control.spec.ts.
 *
 * Behavior under test:
 *  - Renders one card per capability, listing configured models.
 *  - The cheapest model per capability is marked "IN USE".
 *  - Add/remove/reseed are gated behind an in-memory platform admin key.
 *  - Empty and error states for the registry query.
 */
import { test, expect, type Page } from '@playwright/test';
import { setupAuth } from './helpers/auth';

const REGISTRY = {
  total: 2,
  capabilities: [
    {
      capability: 'text_generation',
      selected_model_id: 'cheap-llm',
      models: [
        { provider: 'nvidia', model_id: 'pricey-llm', cost_per_1k_input: 0.02, supports_tools: true, supports_vision: false },
        { provider: 'custom', model_id: 'cheap-llm', cost_per_1k_input: 0, supports_tools: true, supports_vision: false },
      ],
    },
  ],
};

const EMPTY_REGISTRY = { total: 0, capabilities: [] };

async function mockModelsApi(page: Page, opts: { registry?: unknown } = {}): Promise<void> {
  await page.route(/localhost:8000\/models\/configured/, async (route) => {
    const method = route.request().method();
    const url = route.request().url();

    if (url.includes('/reseed')) {
      return route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({ status: 'ok', configured_models: 3 }),
      });
    }
    if (method === 'POST') {
      return route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({ status: 'ok', model_id: 'gpt-oss-20b' }),
      });
    }
    if (method === 'DELETE') {
      return route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify({ status: 'ok', removed: true }) });
    }
    return route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify(opts.registry ?? REGISTRY),
    });
  });
}

test.describe('Model Registry — loading & rendering', () => {
  test('1. loads and renders the registry with capability cards', async ({ page }) => {
    await setupAuth(page);
    await mockModelsApi(page);
    await page.goto('/models');

    await expect(page.getByRole('heading', { name: /Model Registry/i })).toBeVisible({ timeout: 10000 });
    await expect(page.getByText('Reasoning')).toBeVisible();
    await expect(page.getByText('Embeddings')).toBeVisible();
    await expect(page.getByText('cheap-llm')).toBeVisible();
    await expect(page.getByText('pricey-llm')).toBeVisible();
  });

  test('2. marks the cheapest model as IN USE', async ({ page }) => {
    await setupAuth(page);
    await mockModelsApi(page);
    await page.goto('/models');

    await expect(page.getByText('cheap-llm')).toBeVisible({ timeout: 10000 });
    await expect(page.getByText(/IN USE \(cheapest\)/i)).toBeVisible();
  });

  test('3. shows an empty-state hint per capability when nothing is configured', async ({ page }) => {
    await setupAuth(page);
    await mockModelsApi(page, { registry: EMPTY_REGISTRY });
    await page.goto('/models');

    await expect(page.getByRole('heading', { name: /Model Registry/i })).toBeVisible({ timeout: 10000 });
    await expect(page.getByText(/No model configured for embeddings/i)).toBeVisible();
    await expect(page.getByText(/No model configured for reasoning/i)).toBeVisible();
  });

  test('4. shows an error message when the registry request fails', async ({ page }) => {
    await setupAuth(page);
    await page.route(/localhost:8000\/models\/configured/, (route) =>
      route.fulfill({ status: 500, contentType: 'text/plain', body: 'internal error' })
    );
    await page.goto('/models');

    await expect(page.getByText(/Failed to load the model registry/i)).toBeVisible({ timeout: 10000 });
  });
});

test.describe('Model Registry — admin gating', () => {
  test('5. add/reseed actions are disabled until an admin key is entered', async ({ page }) => {
    await setupAuth(page);
    await mockModelsApi(page);
    await page.goto('/models');

    await expect(page.getByText('cheap-llm')).toBeVisible({ timeout: 10000 });
    const addBtn = page.getByRole('button', { name: /Add Model/i });
    const reseedBtn = page.getByRole('button', { name: /Reseed from config/i });
    await expect(addBtn).toBeDisabled();
    await expect(reseedBtn).toBeDisabled();

    await page.getByPlaceholder(/Platform admin key/i).fill('admin-secret');
    await expect(addBtn).toBeEnabled();
    await expect(reseedBtn).toBeEnabled();
  });

  test('6. reseed sends a POST once the admin key is present', async ({ page }) => {
    let reseeded = false;
    await setupAuth(page);
    await mockModelsApi(page);
    await page.route('**/models/configured/reseed', (route) => {
      reseeded = true;
      return route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify({ status: 'ok', configured_models: 3 }) });
    });
    await page.goto('/models');

    await expect(page.getByText('cheap-llm')).toBeVisible({ timeout: 10000 });
    await page.getByPlaceholder(/Platform admin key/i).fill('admin-secret');
    await page.getByRole('button', { name: /Reseed from config/i }).click();

    await expect(async () => expect(reseeded).toBe(true)).toPass({ timeout: 5000 });
  });
});

test.describe('Model Registry — add / remove a model', () => {
  test('7. add-model modal validates required fields before posting', async ({ page }) => {
    await setupAuth(page);
    await mockModelsApi(page);
    await page.goto('/models');

    await expect(page.getByText('cheap-llm')).toBeVisible({ timeout: 10000 });
    await page.getByPlaceholder(/Platform admin key/i).fill('admin-secret');
    await page.getByRole('button', { name: /Add Model/i }).click();

    await page.getByRole('button', { name: /^Save$/i }).click();
    await expect(page.getByRole('alert')).toContainText(/Model ID and at least one capability/i);
  });

  test('8. filling the model id and saving posts the new model and closes the modal', async ({ page }) => {
    let posted = false;
    await setupAuth(page);
    await mockModelsApi(page);
    await page.route(/localhost:8000\/models\/configured$/, async (route) => {
      if (route.request().method() === 'POST') {
        posted = true;
        return route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify({ status: 'ok', model_id: 'gpt-oss-20b' }) });
      }
      return route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(REGISTRY) });
    });
    await page.goto('/models');

    await expect(page.getByText('cheap-llm')).toBeVisible({ timeout: 10000 });
    await page.getByPlaceholder(/Platform admin key/i).fill('admin-secret');
    await page.getByRole('button', { name: /Add Model/i }).click();
    await page.getByPlaceholder(/openai\/gpt-oss-20b/i).fill('gpt-oss-20b');
    await page.getByRole('button', { name: /^Save$/i }).click();

    await expect(async () => expect(posted).toBe(true)).toPass({ timeout: 5000 });
    await expect(page.getByPlaceholder(/openai\/gpt-oss-20b/i)).toBeHidden({ timeout: 5000 });
  });

  test('9. removing a model sends a DELETE for that model', async ({ page }) => {
    let deleted = false;
    await setupAuth(page);
    await mockModelsApi(page);
    await page.route(/localhost:8000\/models\/configured\/.+/, (route) => {
      if (route.request().method() === 'DELETE') {
        deleted = true;
        return route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify({ status: 'ok', removed: true }) });
      }
      return route.continue();
    });
    await page.goto('/models');

    await expect(page.getByText('pricey-llm')).toBeVisible({ timeout: 10000 });
    await page.getByPlaceholder(/Platform admin key/i).fill('admin-secret');
    await page.getByRole('button', { name: /Remove pricey-llm/i }).click();

    await expect(async () => expect(deleted).toBe(true)).toPass({ timeout: 5000 });
  });
});
