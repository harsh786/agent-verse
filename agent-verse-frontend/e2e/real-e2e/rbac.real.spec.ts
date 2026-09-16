/**
 * Real E2E tests for the RBAC feature — NO HTTP mocking.
 *
 * Route:   /rbac                          (src/features/rbac/RbacPage.tsx)
 * Backend: GET/POST   /tenants/me/roles
 *          DELETE     /tenants/me/roles/{role_id}
 *          GET/POST   /tenants/me/ip-allowlist
 *          DELETE     /tenants/me/ip-allowlist/{entry_id}
 *          (src/app/api/tenants.py, prefix "/tenants")
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
  tenant = await createE2ETenant(ctx, `-rbac-${Math.random().toString(36).slice(2, 7)}`);
  api = apiClient(ctx, tenant);
});

base.describe('RBAC — role assignments (real CRUD)', () => {
  base('roles list returns an array', async () => {
    const resp = await api.get('/tenants/me/roles');
    expect(resp.status()).toBe(200);
    const body = await resp.json();
    expect(Array.isArray(body)).toBe(true);
  });

  base('create, list, then delete a role assignment via real API', async () => {
    const createResp = await api.post('/tenants/me/roles', {
      user_id: `e2e-user-${Date.now()}`,
      role: 'viewer',
    });
    expect(createResp.status()).toBe(201);
    const created = await createResp.json();
    expect(created.id).toBeTruthy();
    expect(created.role).toBe('viewer');

    const listResp = await api.get('/tenants/me/roles');
    const list = await listResp.json();
    expect(list.some((r: { id: string }) => r.id === created.id)).toBe(true);

    const deleteResp = await api.delete(`/tenants/me/roles/${created.id}`);
    expect(deleteResp.status()).toBe(204);
  });

  base('creating a role with an invalid role name is rejected', async () => {
    const resp = await api.post('/tenants/me/roles', {
      user_id: 'e2e-bad-role-user',
      role: 'superadmin',
    });
    expect(resp.status()).toBe(422);
  });
});

base.describe('RBAC — IP allowlist (real CRUD)', () => {
  base('ip allowlist returns an array', async () => {
    const resp = await api.get('/tenants/me/ip-allowlist');
    expect(resp.status()).toBe(200);
    const body = await resp.json();
    expect(Array.isArray(body)).toBe(true);
  });

  base('create, list, then delete an IP allowlist entry via real API', async () => {
    const createResp = await api.post('/tenants/me/ip-allowlist', {
      cidr: '10.20.0.0/24',
      description: 'e2e office range',
    });
    expect(createResp.status()).toBe(201);
    const created = await createResp.json();
    expect(created.id).toBeTruthy();
    expect(created.cidr).toBe('10.20.0.0/24');

    const listResp = await api.get('/tenants/me/ip-allowlist');
    const list = await listResp.json();
    expect(list.some((e: { id: string }) => e.id === created.id)).toBe(true);

    const deleteResp = await api.delete(`/tenants/me/ip-allowlist/${created.id}`);
    expect(deleteResp.status()).toBe(204);
  });

  base('creating an entry with an invalid CIDR is rejected', async () => {
    const resp = await api.post('/tenants/me/ip-allowlist', {
      cidr: 'not-a-cidr',
      description: 'bad',
    });
    expect(resp.status()).toBe(422);
  });
});

base.describe('RBAC — page rendering', () => {
  base('rbac page renders a role created via the real API', async ({ page }) => {
    const userId = `e2e-visible-${Date.now()}`;
    const createResp = await api.post('/tenants/me/roles', { user_id: userId, role: 'operator' });
    expect(createResp.status()).toBe(201);

    await loginFrontend(page, tenant);
    await page.goto(`${FRONTEND_BASE}/rbac`, { waitUntil: 'networkidle' });
    await expect(page.locator('body')).toBeVisible();
    const text = await page.locator('body').textContent();
    expect(text!.toLowerCase()).not.toContain('internal server error');
    await expect(page.getByText(userId)).toBeVisible({ timeout: 10_000 });
  });
});
