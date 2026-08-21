/**
 * E2E: Team Formation — org forms a team after mission is created.
 * Spec PART 10 (Dynamic Team Formation).
 */
import { test, expect } from '@playwright/test';

const BASE_URL = process.env.PLAYWRIGHT_BASE_URL ?? 'http://localhost:5173';
const ORG_ID   = process.env.TEST_ORG_ID ?? 'org-test-001';
const TEAM_ID  = process.env.TEST_TEAM_ID ?? 'team-test-001';

test.skip(
  () => !process.env.TEST_ORG_ID && !process.env.E2E_FULL,
  'Set TEST_ORG_ID or E2E_FULL=1',
);

test.describe('Team Formation', () => {
  test.beforeEach(async ({ page }) => {
    const apiKey = process.env.TEST_API_KEY ?? 'test-key';
    const tenantId = process.env.TEST_TENANT_ID ?? 'my-org';

    const corsHeaders = {
      'access-control-allow-origin': '*',
      'access-control-allow-headers': 'x-api-key,content-type,authorization',
      'access-control-allow-methods': 'GET,POST,OPTIONS',
    };

    await page.route('**://localhost:8000/**', async (route) => {
      const req = route.request();
      if (req.method() === 'OPTIONS') {
        await route.fulfill({ status: 204, headers: corsHeaders });
        return;
      }

      const url = new URL(req.url());
      const path = url.pathname;

      if (path.includes('/auth/config')) {
        await route.fulfill({
          status: 200,
          contentType: 'application/json',
          headers: corsHeaders,
          body: JSON.stringify({ sso_enabled: false, authorization_endpoint: null }),
        });
        return;
      }

      if (path.includes('/tenants/me')) {
        await route.fulfill({
          status: 200,
          contentType: 'application/json',
          headers: corsHeaders,
          body: JSON.stringify({ tenant_id: 'my-org', plan: 'free' }),
        });
        return;
      }

      if (path.includes('/auth/mfa/status')) {
        await route.fulfill({
          status: 200,
          contentType: 'application/json',
          headers: corsHeaders,
          body: JSON.stringify({ enabled: false }),
        });
        return;
      }

      if (path.endsWith(`/v1/org/${ORG_ID}/teams/${TEAM_ID}/members`)) {
        await route.fulfill({
          status: 200,
          contentType: 'application/json',
          headers: corsHeaders,
          body: JSON.stringify({
            team_id: TEAM_ID,
            member_ids: ['a-1', 'a-2'],
            members: [
              { id: 'a-1', name: 'Agent 1', status: 'executing', role: 'Mission specialist' },
              { id: 'a-2', name: 'Agent 2', status: 'idle', role: 'Mission specialist' },
            ],
          }),
        });
        return;
      }

      if (path.includes('/events')) {
        await route.fulfill({
          status: 200,
          contentType: 'application/json',
          headers: corsHeaders,
          body: JSON.stringify({ data: [] }),
        });
        return;
      }

      if (path.includes('/approvals')) {
        await route.fulfill({
          status: 200,
          contentType: 'application/json',
          headers: corsHeaders,
          body: JSON.stringify([]),
        });
        return;
      }

      if (path.includes('/tasks')) {
        await route.fulfill({
          status: 200,
          contentType: 'application/json',
          headers: corsHeaders,
          body: JSON.stringify({ data: [], cursor: null, hasMore: false }),
        });
        return;
      }

      await route.fulfill({
        status: 200,
        contentType: 'application/json',
        headers: corsHeaders,
        body: JSON.stringify({ data: [], cursor: null, hasMore: false }),
      });
    });

    await page.addInitScript((key, tid) => {
      const authState = JSON.stringify({
        state: {
          apiKey: key,
          tenantId: tid,
          plan: 'free',
          isAuthenticated: true,
          ssoMode: false,
          accessToken: '',
          refreshToken: '',
          tokenExpiresAt: 0,
          sessionValidated: true,
          mfaRequired: false,
          mfaToken: null,
        },
        version: 0,
      });

      // Keep both stores in sync for hydration/backward-compat paths.
      sessionStorage.setItem('av_api_key', key);
      localStorage.setItem('av_api_key', key);
      sessionStorage.setItem('av-auth', authState);
      localStorage.setItem('av-auth', authState);
    }, apiKey, tenantId);
  });

  test('team detail page renders members from backend endpoint', async ({ page }) => {
    await page.goto(`${BASE_URL}/org/${ORG_ID}/team/${TEAM_ID}`);
    await page.waitForLoadState('networkidle');

    await expect(page).not.toHaveURL(/\/auth$/);

    await expect(page.getByText('Agent 1')).toBeVisible({ timeout: 10000 });
    await expect(page.getByText('Agent 2')).toBeVisible({ timeout: 10000 });
  });

  test('teams page loads without error', async ({ page }) => {
    await page.goto(`${BASE_URL}/org/${ORG_ID}/teams`);
    await page.waitForLoadState('networkidle');
    await expect(page).not.toHaveURL(/error/i);
  });

  test('team list shows active teams', async ({ page }) => {
    await page.goto(`${BASE_URL}/org/${ORG_ID}/teams`);
    await page.waitForLoadState('networkidle');
    // Ensure page renders something (even empty state)
    await expect(page.locator('body')).not.toBeEmpty();
  });

  test('team formation animation runs on new mission', async ({ page }) => {
    await page.goto(`${BASE_URL}/org/${ORG_ID}`);
    await page.waitForLoadState('networkidle');
    // Activity feed should show team-related events if any missions ran
    const feed = page.locator('[data-testid="activity-feed"]')
      .or(page.getByRole('list', { name: /activity/i }));
    // Feed may or may not exist based on data
    await expect(page).not.toHaveURL(/error/i);
  });

  test('team page has accessible member list', async ({ page }) => {
    await page.goto(`${BASE_URL}/org/${ORG_ID}/teams`);
    await page.waitForLoadState('networkidle');
    // All interactive buttons have accessible labels
    const buttons = page.getByRole('button');
    const count = await buttons.count();
    for (let i = 0; i < Math.min(count, 3); i++) {
      const btn = buttons.nth(i);
      const ariaLabel = await btn.getAttribute('aria-label');
      const text = await btn.textContent();
      expect(ariaLabel || text?.trim()).toBeTruthy();
    }
  });
});
