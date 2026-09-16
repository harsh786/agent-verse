/**
 * Prompt Variants — E2E Tests
 *
 * A/B testing dashboard for prompt templates.
 * Endpoints exercised:
 *   GET    /intelligence/prompt-variants            (list)
 *   GET    /intelligence/prompt-variants/{key}       (filtered by key)
 *   POST   /intelligence/prompt-variants             (create)
 *   POST   /intelligence/prompt-variants/{key}/{id}/promote
 *   DELETE /intelligence/prompt-variants/{key}/{id}
 */
import { test, expect, type Page } from '@playwright/test';
import { setupAuth } from './helpers/auth';

interface PromptVariant {
  variant_id: string;
  key: string;
  label: string;
  content: string;
  is_active: boolean;
  win_rate?: number;
  usage_count?: number;
  created_at: string;
}

function baseVariants(): PromptVariant[] {
  return [
    {
      variant_id: 'v1',
      key: 'system_prompt',
      label: 'Concise',
      content: 'You are a concise assistant.',
      is_active: true,
      win_rate: 0.62,
      usage_count: 120,
      created_at: '2026-01-01T00:00:00Z',
    },
    {
      variant_id: 'v2',
      key: 'system_prompt',
      label: 'Verbose',
      content: 'You are a very verbose and thorough assistant that explains everything in detail.',
      is_active: false,
      win_rate: 0.38,
      usage_count: 45,
      created_at: '2026-01-02T00:00:00Z',
    },
    {
      variant_id: 'v3',
      key: 'greeting',
      label: 'Friendly',
      content: 'Hello! How can I help today?',
      is_active: true,
      usage_count: 10,
      created_at: '2026-01-03T00:00:00Z',
    },
  ];
}

async function mockPromptVariantsApi(page: Page, variants: PromptVariant[]): Promise<void> {
  await page.route(/localhost:8000\/intelligence\/prompt-variants/, async (route) => {
    const method = route.request().method();
    const url = route.request().url();

    if (method === 'POST' && url.endsWith('/promote')) {
      const m = url.match(/\/prompt-variants\/([^/]+)\/([^/]+)\/promote$/);
      if (m) {
        const [, key, variantId] = m;
        variants.forEach((v) => {
          if (v.key === key) v.is_active = v.variant_id === variantId;
        });
      }
      return route.fulfill({ status: 200, contentType: 'application/json', body: '{}' });
    }

    if (method === 'DELETE') {
      const m = url.match(/\/prompt-variants\/[^/]+\/([^/]+)$/);
      if (m) {
        const idx = variants.findIndex((v) => v.variant_id === m[1]);
        if (idx >= 0) variants.splice(idx, 1);
      }
      return route.fulfill({ status: 204, body: '' });
    }

    if (method === 'POST') {
      const body = JSON.parse(route.request().postData() ?? '{}') as {
        key: string;
        label: string;
        content: string;
      };
      const created: PromptVariant = {
        variant_id: `v-new-${variants.length + 1}`,
        key: body.key,
        label: body.label,
        content: body.content,
        is_active: false,
        usage_count: 0,
        created_at: new Date().toISOString(),
      };
      variants.push(created);
      return route.fulfill({ status: 201, contentType: 'application/json', body: JSON.stringify(created) });
    }

    const keyMatch = url.match(/\/prompt-variants\/([^/?]+)$/);
    if (method === 'GET' && keyMatch) {
      const key = decodeURIComponent(keyMatch[1]);
      return route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify(variants.filter((v) => v.key === key)),
      });
    }

    return route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(variants) });
  });
}

test.describe('Prompt Variants — Load & Populated State', () => {
  test('1. Loads and renders the header and populated variant cards', async ({ page }) => {
    await setupAuth(page);
    await mockPromptVariantsApi(page, baseVariants());
    await page.goto('/prompt-variants');

    await expect(page.getByRole('heading', { name: /prompt variants/i })).toBeVisible({ timeout: 10000 });
    await expect(page.getByText('Concise')).toBeVisible();
    await expect(page.getByText('Verbose')).toBeVisible();
    await expect(page.getByText('Friendly')).toBeVisible();
    await expect(page.getByText('Active').first()).toBeVisible();
    await expect(page.getByText(/120 uses/i)).toBeVisible();
    await expect(page.getByText(/62% win rate/i)).toBeVisible();
  });

  test('2. Empty state shows "No prompt variants yet"', async ({ page }) => {
    await setupAuth(page);
    await mockPromptVariantsApi(page, []);
    await page.goto('/prompt-variants');

    await expect(page.getByRole('heading', { name: /prompt variants/i })).toBeVisible({ timeout: 10000 });
    await expect(page.getByText(/no prompt variants yet/i)).toBeVisible({ timeout: 5000 });
  });

  test('3. Error state shows the failure alert when the list request fails', async ({ page }) => {
    await setupAuth(page);
    await page.route(/localhost:8000\/intelligence\/prompt-variants$/, (route) =>
      route.fulfill({ status: 500, contentType: 'application/json', body: '{"detail":"boom"}' })
    );
    await page.goto('/prompt-variants');

    await expect(page.getByRole('heading', { name: /prompt variants/i })).toBeVisible({ timeout: 10000 });
    await expect(page.getByRole('alert')).toContainText(/failed to load prompt variants/i, { timeout: 5000 });
  });
});

test.describe('Prompt Variants — Filtering', () => {
  test('4. Key filter chips narrow the list to variants of that key', async ({ page }) => {
    await setupAuth(page);
    await mockPromptVariantsApi(page, baseVariants());
    await page.goto('/prompt-variants');

    await expect(page.getByRole('heading', { name: /prompt variants/i })).toBeVisible({ timeout: 10000 });
    await expect(page.getByText('system_prompt', { exact: false }).first()).toBeVisible();

    await page.getByRole('button', { name: /^greeting/i }).click();

    await expect(page.getByText('Friendly')).toBeVisible({ timeout: 5000 });
    await expect(page.getByText('Concise')).not.toBeVisible();
    await expect(page.getByText('Verbose')).not.toBeVisible();

    // Switch back to All
    await page.getByRole('button', { name: /^all$/i }).click();
    await expect(page.getByText('Concise')).toBeVisible({ timeout: 5000 });
  });
});

test.describe('Prompt Variants — Mutations (primary interactions)', () => {
  test('5. Creating a new variant adds it to the list', async ({ page }) => {
    await setupAuth(page);
    await mockPromptVariantsApi(page, baseVariants());
    await page.goto('/prompt-variants');

    await expect(page.getByRole('heading', { name: /prompt variants/i })).toBeVisible({ timeout: 10000 });
    await page.getByRole('button', { name: /new variant/i }).click();

    await expect(page.getByRole('dialog')).toBeVisible({ timeout: 5000 });
    await page.getByPlaceholder('system_prompt').fill('followup_prompt');
    await page.getByPlaceholder('Concise variant').fill('Terse Followup');
    await page.getByPlaceholder(/you are a helpful assistant/i).fill('Keep follow-up questions short.');
    await page.getByRole('button', { name: /^create variant$/i }).click();

    await expect(page.getByRole('dialog')).not.toBeVisible({ timeout: 5000 });
    await expect(page.getByText('Terse Followup')).toBeVisible({ timeout: 5000 });
  });

  test('6. Promoting a variant marks it active and demotes the previous active one', async ({ page }) => {
    await setupAuth(page);
    await mockPromptVariantsApi(page, baseVariants());
    await page.goto('/prompt-variants');

    await expect(page.getByRole('heading', { name: /prompt variants/i })).toBeVisible({ timeout: 10000 });
    await expect(page.getByText('Concise')).toBeVisible();

    const verboseCard = page.locator('div', { hasText: 'Verbose' }).filter({ has: page.getByTitle('Promote to active') }).first();
    await verboseCard.getByTitle('Promote to active').click();

    await expect(page.getByTitle('Promote to active').filter({ hasText: '' })).toHaveCount(1, { timeout: 5000 });
  });

  test('7. Deleting a variant removes it from the list', async ({ page }) => {
    await setupAuth(page);
    await mockPromptVariantsApi(page, baseVariants());
    await page.goto('/prompt-variants');

    await expect(page.getByRole('heading', { name: /prompt variants/i })).toBeVisible({ timeout: 10000 });
    await expect(page.getByText('Friendly')).toBeVisible();

    const friendlyCard = page.locator('div.rounded-xl', { hasText: 'Friendly' }).first();
    await friendlyCard.getByTitle('Delete variant').click();

    await expect(page.getByText('Friendly')).not.toBeVisible({ timeout: 5000 });
  });

  test('8. Copy button copies content and shows a success toast', async ({ page }) => {
    await setupAuth(page);
    await mockPromptVariantsApi(page, baseVariants());
    await page.context().grantPermissions(['clipboard-read', 'clipboard-write']).catch(() => undefined);
    await page.goto('/prompt-variants');

    await expect(page.getByRole('heading', { name: /prompt variants/i })).toBeVisible({ timeout: 10000 });
    await page.getByTitle('Copy content').first().click();

    await expect(page.getByText(/copied to clipboard/i)).toBeVisible({ timeout: 5000 });
  });
});
