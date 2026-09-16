/**
 * Real E2E tests for the Prompt Variants feature — NO HTTP mocking.
 *
 * Route:   /prompt-variants                          (src/features/prompt-variants/PromptVariantsPage.tsx)
 * Backend: GET    /intelligence/prompt-variants[?key=]
 *          POST   /intelligence/prompt-variants
 *          POST   /intelligence/prompt-variants/{variant_id}/promote
 *          DELETE /intelligence/prompt-variants/{variant_id}
 *          (src/app/api/enterprise.py, intelligence_router prefix "/intelligence")
 *
 * NOTE: while wiring this spec up against the real backend, the frontend page
 * (src/features/prompt-variants/PromptVariantsPage.tsx) was found to be using a
 * stale response contract (field names `variant_id`/`label`/`content`/`is_active`
 * instead of the real `id`/`name`/`prompt_text`/`is_control`, a create body of
 * `{key,label,content}` instead of `{key,name,prompt_text}`, and promote/delete
 * URLs with a spurious `/{key}/` path segment). That was a real bug breaking the
 * page against the live backend, so it was fixed minimally in the same file
 * (and its co-located unit test, PromptVariantsPage.test.tsx, updated to match).
 *
 * Every HTTP request goes through:
 *   browser → localhost:5173 (Vite) → localhost:8000 (FastAPI) → Postgres+Redis
 *
 * Tenant signup is capped at 10/IP/hour on the backend (app/api/tenants.py) and
 * that IP is shared by every real-e2e worker/file running concurrently, so this
 * file creates exactly ONE tenant (in beforeAll) and reuses it for every test.
 */

import { expect, test as base } from '@playwright/test';
import { createE2ETenant, loginFrontend, apiClient, FRONTEND_BASE, type E2ETenant } from './fixtures';

let tenant: E2ETenant;
let api: ReturnType<typeof apiClient>;

base.beforeAll(async ({ playwright }) => {
  const ctx = await playwright.request.newContext();
  tenant = await createE2ETenant(ctx, `-promptvar-${Math.random().toString(36).slice(2, 7)}`);
  api = apiClient(ctx, tenant);
});

base.describe('Prompt Variants — real CRUD', () => {
  base('list returns an array', async () => {
    const resp = await api.get('/intelligence/prompt-variants');
    expect(resp.status()).toBe(200);
    const body = await resp.json();
    expect(Array.isArray(body)).toBe(true);
  });

  base('create a variant, filter by key, promote it, then delete it', async () => {
    const key = `e2e_prompt_${Date.now()}`;

    const createResp = await api.post('/intelligence/prompt-variants', {
      key,
      name: 'Concise variant',
      prompt_text: 'Be brief and to the point.',
    });
    expect(createResp.status()).toBe(201);
    const created = await createResp.json();
    expect(created.id).toBeTruthy();
    expect(created.key).toBe(key);
    expect(created.name).toBe('Concise variant');
    expect(created.prompt_text).toBe('Be brief and to the point.');
    expect(created.is_control).toBe(false);

    const filteredResp = await api.get(`/intelligence/prompt-variants?key=${key}`);
    expect(filteredResp.status()).toBe(200);
    const filtered = await filteredResp.json();
    expect(filtered).toHaveLength(1);
    expect(filtered[0].id).toBe(created.id);

    const promoteResp = await api.post(`/intelligence/prompt-variants/${created.id}/promote`);
    expect(promoteResp.status()).toBe(200);
    const promoted = await promoteResp.json();
    expect(promoted.promoted).toBe(true);

    const reportResp = await api.get(`/intelligence/prompt-variants/${created.id}/report`);
    expect(reportResp.status()).toBe(200);

    const deleteResp = await api.delete(`/intelligence/prompt-variants/${created.id}`);
    expect(deleteResp.status()).toBe(204);
  });

  base('creating a variant with a missing field is rejected', async () => {
    const resp = await api.post('/intelligence/prompt-variants', { key: 'incomplete' });
    expect(resp.status()).toBe(422);
  });
});

base.describe('Prompt Variants — page rendering', () => {
  base('prompt-variants page renders a variant created via the real API', async ({ page }) => {
    const key = `e2e_visible_${Date.now()}`;
    const createResp = await api.post('/intelligence/prompt-variants', {
      key,
      name: 'E2E Visible Variant',
      prompt_text: 'You are a helpful assistant used for visibility testing.',
    });
    expect(createResp.status()).toBe(201);

    await loginFrontend(page, tenant);
    await page.goto(`${FRONTEND_BASE}/prompt-variants`, { waitUntil: 'networkidle' });
    await expect(page.locator('body')).toBeVisible();
    const text = await page.locator('body').textContent();
    expect(text!.toLowerCase()).not.toContain('internal server error');
    await expect(page.getByRole('heading', { name: /Prompt Variants/i })).toBeVisible();
    await expect(page.getByText('E2E Visible Variant')).toBeVisible({ timeout: 10_000 });
    // The key renders both in the filter chip and in a <code> tag on the card —
    // scope to the exact <code> element to avoid a strict-mode multi-match.
    await expect(page.locator('code', { hasText: key })).toBeVisible();
  });
});
