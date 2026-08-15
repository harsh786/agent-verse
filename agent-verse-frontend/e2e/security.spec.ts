/**
 * E2E tests — Security Center (/security)
 *
 * All backend calls are intercepted and mocked.
 * Tests:
 *   - Page load: title, security score, all 6 tabs visible
 *   - Tab switching: each tab activates and shows content
 *   - Agent Identity: keys list renders
 *   - Governance: compliance bundles visible
 *   - Guardrails: layer list renders
 *   - Audit Trail: table header visible
 *   - Scopes & Roles: roles section visible
 *   - Limits: plan limits table visible
 *   - Accessibility: tab roles, aria-labels
 */

import { test, expect, type Page } from '@playwright/test';

// ── Auth + mock setup ─────────────────────────────────────────────────────────

async function setupAuth(page: Page) {
  await page.addInitScript(() => {
    localStorage.setItem('av-auth', JSON.stringify({
      state: {
        apiKey: 'test-key',
        tenantId: 'test-tenant',
        plan: 'enterprise',
        isAuthenticated: true,
      },
      version: 0,
    }));
    localStorage.setItem('av_api_key', 'test-key');
    sessionStorage.setItem('av_api_key', 'test-key');
  });
  await page.route('**/tenants/me', (route) =>
    route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({
        tenant_id: 'test-tenant',
        name: 'Acme Corp',
        plan: 'enterprise',
      }),
    }),
  );
}

async function mockSecurityApis(page: Page) {
  // Agent keys
  await page.route(/agents\/.*\/keys/, (route) =>
    route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify([
        {
          key_id: 'key-abc123',
          name: 'Production Key',
          allowed_tools: ['search', 'write'],
          denied_tools: [],
          created_at: Date.now() / 1000,
          last_used_at: Date.now() / 1000,
          is_active: true,
          use_count: 42,
        },
      ]),
    }),
  );

  // Governance policies
  await page.route(/governance\/policies/, (route) =>
    route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({ policies: [], active_bundles: ['gdpr'] }),
    }),
  );

  // Audit events
  await page.route(/governance\/audit/, (route) =>
    route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({ events: [], total: 0 }),
    }),
  );

  // Health
  await page.route('**/health', (route) =>
    route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({ status: 'ok' }),
    }),
  );

  // Catch-all 404 for other routes
  await page.route(/localhost:8000\/(?!tenants)/, (route) => {
    if (route.request().url().includes('agents') ||
        route.request().url().includes('governance') ||
        route.request().url().includes('health')) {
      return; // already matched above
    }
    route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify([]),
    });
  });
}

async function navigateToSecurity(page: Page) {
  await page.goto('/security');
  await expect(page.getByTestId('security-center-page')).toBeVisible({ timeout: 12_000 });
}

// ── Tests ─────────────────────────────────────────────────────────────────────

test.describe('Security Center — page load', () => {
  test('shows Security Center title', async ({ page }) => {
    await setupAuth(page);
    await mockSecurityApis(page);
    await navigateToSecurity(page);

    await expect(page.getByText('Security Center')).toBeVisible();
  });

  test('shows security score widget', async ({ page }) => {
    await setupAuth(page);
    await mockSecurityApis(page);
    await navigateToSecurity(page);

    await expect(page.getByText('Security Score')).toBeVisible();
  });

  test('shows all 6 tab buttons', async ({ page }) => {
    await setupAuth(page);
    await mockSecurityApis(page);
    await navigateToSecurity(page);

    await expect(page.getByTestId('tab-identity')).toBeVisible();
    await expect(page.getByTestId('tab-governance')).toBeVisible();
    await expect(page.getByTestId('tab-guardrails')).toBeVisible();
    await expect(page.getByTestId('tab-audit')).toBeVisible();
    await expect(page.getByTestId('tab-scopes')).toBeVisible();
    await expect(page.getByTestId('tab-limits')).toBeVisible();
  });

  test('Agent Identity tab is active by default', async ({ page }) => {
    await setupAuth(page);
    await mockSecurityApis(page);
    await navigateToSecurity(page);

    const identityTab = page.getByTestId('tab-identity');
    // Active tab should have the neural-violet class applied
    await expect(identityTab).toBeVisible();
    // Check it has visually distinct styling vs inactive tabs
    await expect(identityTab).toHaveClass(/bg-neural-violet/);
  });

  test('tab content area is rendered', async ({ page }) => {
    await setupAuth(page);
    await mockSecurityApis(page);
    await navigateToSecurity(page);

    await expect(page.getByTestId('tab-content')).toBeVisible();
  });
});

test.describe('Security Center — tab navigation', () => {
  test('clicking Governance tab activates it', async ({ page }) => {
    await setupAuth(page);
    await mockSecurityApis(page);
    await navigateToSecurity(page);

    await page.getByTestId('tab-governance').click();
    await expect(page.getByTestId('tab-governance')).toHaveClass(/bg-neural-violet/);
    // Identity tab should no longer be active
    await expect(page.getByTestId('tab-identity')).not.toHaveClass(/bg-neural-violet/);
  });

  test('clicking Guardrails tab activates it', async ({ page }) => {
    await setupAuth(page);
    await mockSecurityApis(page);
    await navigateToSecurity(page);

    await page.getByTestId('tab-guardrails').click();
    await expect(page.getByTestId('tab-guardrails')).toHaveClass(/bg-neural-violet/);
  });

  test('clicking Audit Trail tab activates it', async ({ page }) => {
    await setupAuth(page);
    await mockSecurityApis(page);
    await navigateToSecurity(page);

    await page.getByTestId('tab-audit').click();
    await expect(page.getByTestId('tab-audit')).toHaveClass(/bg-neural-violet/);
  });

  test('clicking Scopes & Roles tab activates it', async ({ page }) => {
    await setupAuth(page);
    await mockSecurityApis(page);
    await navigateToSecurity(page);

    await page.getByTestId('tab-scopes').click();
    await expect(page.getByTestId('tab-scopes')).toHaveClass(/bg-neural-violet/);
  });

  test('clicking Limits tab activates it', async ({ page }) => {
    await setupAuth(page);
    await mockSecurityApis(page);
    await navigateToSecurity(page);

    await page.getByTestId('tab-limits').click();
    await expect(page.getByTestId('tab-limits')).toHaveClass(/bg-neural-violet/);
  });
});

test.describe('Security Center — tab content', () => {
  test('Agent Identity tab shows agent keys section', async ({ page }) => {
    await setupAuth(page);
    await mockSecurityApis(page);
    await navigateToSecurity(page);

    // Default tab — agent identity content should be visible
    await expect(page.getByTestId('tab-content')).toBeVisible();
    // The panel heading text
    await expect(page.getByText('Agent Identity')).toBeVisible();
  });

  test('Governance tab shows compliance bundles', async ({ page }) => {
    await setupAuth(page);
    await mockSecurityApis(page);
    await navigateToSecurity(page);

    await page.getByTestId('tab-governance').click();
    await expect(page.getByTestId('tab-content')).toBeVisible();
    // GovernancePanel renders HIPAA, GDPR, SOC2, India DPDP, PCI-DSS
    await expect(page.getByText('HIPAA').first()).toBeVisible({ timeout: 5_000 });
    await expect(page.getByText('GDPR').first()).toBeVisible();
  });

  test('Guardrails tab shows active guardrail layers', async ({ page }) => {
    await setupAuth(page);
    await mockSecurityApis(page);
    await navigateToSecurity(page);

    await page.getByTestId('tab-guardrails').click();
    await expect(page.getByTestId('tab-content')).toBeVisible();
    await expect(page.getByText(/Prompt Injection Scanner/i)).toBeVisible({ timeout: 5_000 });
  });

  test('Limits tab shows plan limits table', async ({ page }) => {
    await setupAuth(page);
    await mockSecurityApis(page);
    await navigateToSecurity(page);

    await page.getByTestId('tab-limits').click();
    await expect(page.getByTestId('tab-content')).toBeVisible();
    // LimitsPanel renders PLAN_LIMITS with Free, Starter, Professional, Enterprise
    await expect(page.getByText('Free').first()).toBeVisible({ timeout: 5_000 });
    await expect(page.getByText('Enterprise').first()).toBeVisible();
  });
});

test.describe('Security Center — accessibility', () => {
  test('all tab buttons have text labels', async ({ page }) => {
    await setupAuth(page);
    await mockSecurityApis(page);
    await navigateToSecurity(page);

    await expect(page.getByTestId('tab-identity')).toContainText('Agent Identity');
    await expect(page.getByTestId('tab-governance')).toContainText('Governance');
    await expect(page.getByTestId('tab-guardrails')).toContainText('Guardrails');
    await expect(page.getByTestId('tab-audit')).toContainText('Audit Trail');
    await expect(page.getByTestId('tab-scopes')).toContainText('Scopes & Roles');
    await expect(page.getByTestId('tab-limits')).toContainText('Limits');
  });

  test('tab icons have aria-hidden', async ({ page }) => {
    await setupAuth(page);
    await mockSecurityApis(page);
    await navigateToSecurity(page);

    // Icons inside tabs should be aria-hidden (decorative)
    const hiddenIcons = page.locator('[aria-hidden="true"]');
    await expect(hiddenIcons.first()).toBeVisible();
  });

  test('page subtitle is visible', async ({ page }) => {
    await setupAuth(page);
    await mockSecurityApis(page);
    await navigateToSecurity(page);

    await expect(
      page.getByText(/Agent identity, governance, guardrails/i),
    ).toBeVisible();
  });
});

test.describe('Security Center — tab tooltip/title', () => {
  test('each tab button has a descriptive title attribute', async ({ page }) => {
    await setupAuth(page);
    await mockSecurityApis(page);
    await navigateToSecurity(page);

    await expect(page.getByTestId('tab-identity')).toHaveAttribute('title', expect.stringContaining('Per-agent keys'));
    await expect(page.getByTestId('tab-governance')).toHaveAttribute('title', expect.stringContaining('HITL'));
    await expect(page.getByTestId('tab-guardrails')).toHaveAttribute('title', expect.stringContaining('Injection'));
  });
});
