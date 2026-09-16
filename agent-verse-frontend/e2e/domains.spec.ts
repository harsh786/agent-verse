/**
 * Domains — E2E Tests
 *
 * Covers /domains (list) and /domains/:domain (detail):
 *   1. Domains list — renders, search empty state, populated counts, card navigation
 *   2. Domain detail — renders hero, empty state, populated templates, deploy interaction
 */
import { test, expect } from '@playwright/test';
import { setupAuth } from './helpers/auth';
import type { Page } from '@playwright/test';
import type { MarketplaceV2Template, GoalTemplate } from '../src/lib/api/client';

// ── Mock data helpers ─────────────────────────────────────────────────────────

function makeMarketplaceTemplate(overrides: Partial<MarketplaceV2Template> = {}): MarketplaceV2Template {
  return {
    template_id: 't-1',
    slug: 'hr-onboarder',
    name: 'HR Onboarder',
    description: 'Automates new-hire onboarding end to end.',
    domain: 'hr-talent',
    required_connectors: [],
    autonomy_mode: 'supervised',
    visibility: 'public',
    review_status: 'approved',
    is_builtin: true,
    is_verified: true,
    install_count: 12,
    version: '1.0.0',
    ...overrides,
  };
}

function makeGoalTemplate(overrides: Partial<GoalTemplate> = {}): GoalTemplate {
  return {
    id: 'gt-1',
    name: 'Onboard new hire',
    description: 'Kick off the onboarding checklist for a new employee.',
    goal_text: 'Onboard {{name}} starting {{date}}',
    domain: 'hr-talent',
    parameters: [],
    use_count: 4,
    version: 1,
    created_at: new Date().toISOString(),
    ...overrides,
  };
}

/** Mock GET /marketplace/templates (list, optionally domain-filtered) and POST deploy. */
async function mockMarketplaceApi(
  page: Page,
  items: MarketplaceV2Template[] = [],
  opts: { deployResult?: { agent_id: string; agent_name?: string } } = {}
): Promise<void> {
  await page.route(/localhost:8000\/marketplace\/templates/, async (route) => {
    const req = route.request();
    const url = new URL(req.url());

    if (req.method() === 'POST' && url.pathname.includes('/deploy')) {
      return route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify(
          opts.deployResult
            ? { success: true, ...opts.deployResult }
            : { success: true, agent_id: 'agent-new-1', agent_name: 'HR Onboarder Agent' }
        ),
      });
    }

    const domainFilter = url.searchParams.get('domain');
    const filtered = domainFilter
      ? items.filter((t) => t.domain === domainFilter)
      : items;

    return route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({ items: filtered, total: filtered.length, page: 1, page_size: 50 }),
    });
  });
}

/** Mock GET /templates (list, optionally domain-filtered). */
async function mockTemplatesApi(page: Page, items: GoalTemplate[] = []): Promise<void> {
  await page.route(/localhost:8000\/templates/, async (route) => {
    const url = new URL(route.request().url());
    const domainFilter = url.searchParams.get('domain');
    const filtered = domainFilter ? items.filter((t) => t.domain === domainFilter) : items;
    return route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify(filtered),
    });
  });
}

// ═══════════════════════════════════════════════════════════════════════════════
// SUITE 1 — Domains list (/domains)
// ═══════════════════════════════════════════════════════════════════════════════

test.describe('Domains — list page', () => {
  test('1. Renders heading, search box, and domain cards', async ({ page }) => {
    await setupAuth(page);
    await mockMarketplaceApi(page, []);
    await mockTemplatesApi(page, []);

    await page.goto('/domains');

    await expect(page.getByRole('heading', { name: /domain solutions/i })).toBeVisible({ timeout: 10000 });
    await expect(page.getByLabel(/search domains/i)).toBeVisible();
    await expect(page.getByLabel('Explore HR & Talent')).toBeVisible();
    await expect(page.getByLabel('Explore Software Engineering')).toBeVisible();
  });

  test('2. Empty state — search with no matches shows "No domains match"', async ({ page }) => {
    await setupAuth(page);
    await mockMarketplaceApi(page, []);
    await mockTemplatesApi(page, []);

    await page.goto('/domains');
    await expect(page.getByRole('heading', { name: /domain solutions/i })).toBeVisible({ timeout: 10000 });

    await page.getByLabel(/search domains/i).fill('zzz-nonexistent-domain');

    await expect(page.getByText(/no domains match/i)).toBeVisible({ timeout: 5000 });
    await expect(page.getByLabel('Explore HR & Talent')).not.toBeVisible();
  });

  test('3. Populated state — aggregated agent/template counts show on matching card', async ({ page }) => {
    await setupAuth(page);
    await mockMarketplaceApi(page, [
      makeMarketplaceTemplate({ template_id: 't-1', domain: 'hr-talent' }),
      makeMarketplaceTemplate({ template_id: 't-2', domain: 'hr-talent' }),
    ]);
    await mockTemplatesApi(page, [makeGoalTemplate({ id: 'gt-1', domain: 'hr-talent' })]);

    await page.goto('/domains');
    await expect(page.getByRole('heading', { name: /domain solutions/i })).toBeVisible({ timeout: 10000 });

    const hrCard = page.getByLabel('Explore HR & Talent');
    await expect(hrCard).toBeVisible();
    await expect(hrCard.getByText('2 agents')).toBeVisible({ timeout: 5000 });
    await expect(hrCard.getByText('1 template')).toBeVisible();
  });

  test('4. Clicking a domain card navigates to its detail route', async ({ page }) => {
    await setupAuth(page);
    await mockMarketplaceApi(page, []);
    await mockTemplatesApi(page, []);

    await page.goto('/domains');
    await expect(page.getByRole('heading', { name: /domain solutions/i })).toBeVisible({ timeout: 10000 });

    await page.getByLabel('Explore HR & Talent').click();

    await expect(page).toHaveURL(/\/domains\/hr-talent/, { timeout: 5000 });
  });
});

// ═══════════════════════════════════════════════════════════════════════════════
// SUITE 2 — Domain detail (/domains/:domain)
// ═══════════════════════════════════════════════════════════════════════════════

test.describe('Domains — detail page', () => {
  test('5. Renders domain hero, tags, and stats strip', async ({ page }) => {
    await setupAuth(page);
    await mockMarketplaceApi(page, [makeMarketplaceTemplate({ template_id: 't-1', domain: 'hr-talent' })]);
    await mockTemplatesApi(page, [makeGoalTemplate({ id: 'gt-1', domain: 'hr-talent' })]);

    await page.goto('/domains/hr-talent');

    await expect(page.getByRole('heading', { name: 'HR & Talent' })).toBeVisible({ timeout: 10000 });
    await expect(page.getByText('hiring')).toBeVisible();
    await expect(page.getByText('onboarding')).toBeVisible();
    await expect(page.getByText(/1\s+agent template/i)).toBeVisible({ timeout: 5000 });
    await expect(page.getByText(/1\s+goal template/i)).toBeVisible();
  });

  test('6. Empty state — no templates shows "Create Template" CTA', async ({ page }) => {
    await setupAuth(page);
    await mockMarketplaceApi(page, []);
    await mockTemplatesApi(page, []);

    await page.goto('/domains/hr-talent');
    await expect(page.getByRole('heading', { name: 'HR & Talent' })).toBeVisible({ timeout: 10000 });

    await expect(page.getByText(/no templates for hr & talent yet/i)).toBeVisible({ timeout: 5000 });
    await expect(page.getByRole('button', { name: /create template/i })).toBeVisible();
  });

  test('7. Populated state — agent and goal template sections render', async ({ page }) => {
    await setupAuth(page);
    await mockMarketplaceApi(page, [
      makeMarketplaceTemplate({ template_id: 't-1', name: 'HR Onboarder', domain: 'hr-talent' }),
    ]);
    await mockTemplatesApi(page, [
      makeGoalTemplate({ id: 'gt-1', name: 'Onboard new hire', domain: 'hr-talent' }),
    ]);

    await page.goto('/domains/hr-talent');
    await expect(page.getByRole('heading', { name: 'HR & Talent' })).toBeVisible({ timeout: 10000 });

    await expect(page.getByRole('heading', { name: /agent templates/i })).toBeVisible({ timeout: 5000 });
    await expect(page.getByText('HR Onboarder')).toBeVisible();
    await expect(page.getByRole('heading', { name: /goal templates/i })).toBeVisible();
    await expect(page.getByText('Onboard new hire')).toBeVisible();
  });

  test('8. Deploying a no-param agent template shows the "Deployed" badge', async ({ page }) => {
    await setupAuth(page);
    await mockMarketplaceApi(
      page,
      [makeMarketplaceTemplate({ template_id: 't-1', name: 'HR Onboarder', domain: 'hr-talent' })],
      { deployResult: { agent_id: 'agent-abc123456789', agent_name: 'HR Onboarder Agent' } }
    );
    await mockTemplatesApi(page, []);

    await page.goto('/domains/hr-talent');
    await expect(page.getByRole('heading', { name: 'HR & Talent' })).toBeVisible({ timeout: 10000 });
    await expect(page.getByText('HR Onboarder')).toBeVisible({ timeout: 5000 });

    await page.getByRole('button', { name: 'Deploy HR Onboarder' }).click();

    await expect(page.getByText(/deployed/i)).toBeVisible({ timeout: 5000 });
  });
});
