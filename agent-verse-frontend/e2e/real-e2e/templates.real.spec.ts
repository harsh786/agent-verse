/**
 * Real E2E tests for the Templates feature — NO HTTP mocking.
 *
 * Route: /templates (TemplateLibraryPage.tsx)
 * Backend: app/api/templates.py (prefix /templates)
 *
 * NOTE: the backend signup endpoint rate-limits to 10 signups/IP/hour, shared
 * with other test suites/agents on this host. This file creates exactly ONE
 * real tenant (in beforeAll) and reuses it for every test below.
 */
import { expect, request as pwRequest, type APIRequestContext, type Page } from '@playwright/test';
import { test, createE2ETenant, loginFrontend, apiClient, FRONTEND_BASE, type E2ETenant } from './fixtures';

let ctx: APIRequestContext;
let tenant: E2ETenant;
let api: ReturnType<typeof apiClient>;

test.beforeAll(async () => {
  ctx = await pwRequest.newContext();
  tenant = await createE2ETenant(ctx, '-templates');
  api = apiClient(ctx, tenant);
});

test.afterAll(async () => {
  await ctx.dispose();
});

test.describe('Templates — real list and seeding', () => {
  test('GET /templates returns the seeded built-in goal templates', async () => {
    const resp = await api.get('/templates');
    expect(resp.status()).toBe(200);
    const body = await resp.json();
    expect(Array.isArray(body)).toBe(true);
    expect(body.length).toBeGreaterThan(0);
    expect(body[0]).toHaveProperty('goal_text');
    expect(body[0]).toHaveProperty('domain');
  });

  test('GET /templates?domain= filters by domain', async () => {
    const resp = await api.get('/templates?domain=devops');
    expect(resp.status()).toBe(200);
    const body = await resp.json();
    expect(Array.isArray(body)).toBe(true);
    for (const t of body) {
      expect(t.domain).toBe('devops');
    }
  });

  test('GET /templates?search= filters by text match', async () => {
    const resp = await api.get('/templates?search=incident');
    expect(resp.status()).toBe(200);
    const body = await resp.json();
    expect(Array.isArray(body)).toBe(true);
  });
});

test.describe('Templates — real CRUD', () => {
  test('create → get → update → delete a custom template', async () => {
    const createResp = await api.post('/templates', {
      name: 'E2E Custom Template',
      description: 'Created by the real e2e suite',
      goal_text: 'Do {{thing}} for {{target}}',
      domain: 'engineering',
    });
    expect(createResp.status()).toBe(201);
    const created = await createResp.json();
    expect(created.id).toBeTruthy();
    expect(created.parameters).toEqual(
      expect.arrayContaining([
        expect.objectContaining({ name: 'thing' }),
        expect.objectContaining({ name: 'target' }),
      ]),
    );

    const getResp = await api.get(`/templates/${created.id}`);
    expect(getResp.status()).toBe(200);

    const updateResp = await api.put(`/templates/${created.id}`, {
      name: 'E2E Custom Template (updated)',
      description: 'Updated by the real e2e suite',
      goal_text: created.goal_text,
      domain: created.domain,
    });
    expect(updateResp.status()).toBe(204);

    const getAfterUpdate = await api.get(`/templates/${created.id}`);
    const updated = await getAfterUpdate.json();
    expect(updated.name).toBe('E2E Custom Template (updated)');
    expect(updated.version).toBe(2);

    const deleteResp = await api.delete(`/templates/${created.id}`);
    expect(deleteResp.status()).toBe(204);

    const getAfterDelete = await api.get(`/templates/${created.id}`);
    expect(getAfterDelete.status()).toBe(404);
  });

  test('instantiate a template fills in its parameters', async () => {
    const createResp = await api.post('/templates', {
      name: 'E2E Instantiate Template',
      description: 'Created by the real e2e suite',
      goal_text: 'Deploy {{service}} to {{environment}}',
      domain: 'devops',
    });
    expect(createResp.status()).toBe(201);
    const created = await createResp.json();

    const instantiateResp = await api.post(`/templates/${created.id}/instantiate`, {
      parameters: { service: 'checkout-api', environment: 'staging' },
      submit: false,
    });
    expect(instantiateResp.status()).toBe(200);
    const body = await instantiateResp.json();
    expect(body.instantiated_goal).toBe('Deploy checkout-api to staging');
  });

  test('instantiate with missing required parameters returns 422', async () => {
    const createResp = await api.post('/templates', {
      name: 'E2E Missing Params Template',
      description: '',
      goal_text: 'Scale {{deployment}} to {{replicas}} replicas',
      domain: 'devops',
    });
    const created = await createResp.json();

    const resp = await api.post(`/templates/${created.id}/instantiate`, {
      parameters: { deployment: 'checkout-api' },
    });
    expect(resp.status()).toBe(422);
  });

  test('getting an unknown template returns 404', async () => {
    const resp = await api.get('/templates/does-not-exist');
    expect(resp.status()).toBe(404);
  });
});

test.describe('Templates — Library page', () => {
  let page: Page;

  test.beforeEach(async ({ browser }) => {
    page = await browser.newPage();
    await loginFrontend(page, tenant);
  });

  test.afterEach(async () => {
    await page.close();
  });

  test('templates page renders the library', async () => {
    await page.goto(`${FRONTEND_BASE}/templates`, { waitUntil: 'networkidle' });
    await expect(page.locator('body')).toBeVisible();
    const text = await page.locator('body').textContent();
    expect(text!.length).toBeGreaterThan(0);
    expect(text!.toLowerCase()).not.toContain('internal server error');
  });
});
