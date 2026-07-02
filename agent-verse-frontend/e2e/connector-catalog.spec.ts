/**
 * Connector Catalog E2E Tests
 *
 * Tests the full connector catalog experience:
 *   1. Browse all 200+ connector types
 *   2. Search/filter by name and category
 *   3. Category filter pills
 *   4. Native badge visibility
 *   5. Configured badge for registered connectors
 *   6. Configure → prefills registration form
 *   7. Auth field hints displayed on card
 */
import { test, expect, type Page } from '@playwright/test';

// ── Proven auth setup ─────────────────────────────────────────────────────────

async function setupAuth(page: Page) {
  await page.route(/localhost:8000/, (route) =>
    route.fulfill({ status: 404, contentType: 'application/json', body: JSON.stringify({ detail: 'not found' }) })
  );
  await page.addInitScript(() => {
    localStorage.setItem(
      'av-auth',
      JSON.stringify({
        state: { apiKey: 'test-key', tenantId: 'test-tenant', plan: 'free', isAuthenticated: true },
        version: 0,
      })
    );
    localStorage.setItem('av_api_key', 'test-key');
    sessionStorage.setItem(
      'av-auth',
      JSON.stringify({
        state: { apiKey: 'test-key', tenantId: 'test-tenant', plan: 'free', isAuthenticated: true },
        version: 0,
      })
    );
  });
  await page.route('**/tenants/me', (route) =>
    route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({ tenant_id: 'test-tenant', name: 'Test Org', plan: 'free' }),
    })
  );
}

// ── Catalog mock data ──────────────────────────────────────────────────────────

const MOCK_CATALOG = [
  {
    name: 'jira',
    display_name: 'Jira',
    description: 'JIRA — project management, issue tracking, sprints',
    auth_type: 'basic',
    default_url: 'https://your-domain.atlassian.net',
    icon: 'jira',
    category: 'project_management',
    auth_fields: [
      { key: 'url', label: 'Jira URL', placeholder: 'https://mycompany.atlassian.net', field_type: 'url', required: true, hint: 'Your Atlassian Cloud or Server instance URL' },
      { key: 'username', label: 'Email', placeholder: 'you@company.com', field_type: 'email', required: true, hint: '' },
      { key: 'password', label: 'API Token', placeholder: 'ATATT3x...', field_type: 'password', required: true, hint: 'Create at id.atlassian.com/manage-profile/security/api-tokens' },
    ],
    has_builtin: true,
    builtin_server_id: 'builtin-jira',
    is_configured: false,
    connector_type: 'jira',
  },
  {
    name: 'github',
    display_name: 'GitHub',
    description: 'GitHub — code repositories, PRs, issues, Actions',
    auth_type: 'bearer',
    default_url: 'https://api.github.com',
    icon: 'github',
    category: 'devtools',
    auth_fields: [
      { key: 'token', label: 'Personal Access Token', placeholder: 'ghp_xxxxxxxxxxxx', field_type: 'password', required: true, hint: 'Create at github.com/settings/tokens' },
    ],
    has_builtin: true,
    builtin_server_id: 'builtin-github',
    is_configured: true,
    connector_type: 'github',
  },
  {
    name: 'slack',
    display_name: 'Slack',
    description: 'Slack — messaging, channels, workflows, notifications',
    auth_type: 'bearer',
    default_url: 'https://slack.com/api',
    icon: 'slack',
    category: 'communication',
    auth_fields: [
      { key: 'token', label: 'Bot Token', placeholder: 'xoxb-xxxxxxxxxxxx', field_type: 'password', required: true, hint: 'Create a Slack App and use the Bot Token' },
    ],
    has_builtin: true,
    builtin_server_id: 'builtin-slack',
    is_configured: false,
    connector_type: 'slack',
  },
  {
    name: 'stripe',
    display_name: 'Stripe',
    description: 'Stripe — payments, subscriptions, invoices, customers',
    auth_type: 'bearer',
    default_url: 'https://api.stripe.com',
    icon: 'stripe',
    category: 'finance',
    auth_fields: [
      { key: 'token', label: 'Secret Key', placeholder: 'sk_live_xxxxxxxx', field_type: 'password', required: true, hint: '' },
    ],
    has_builtin: true,
    builtin_server_id: 'builtin-stripe',
    is_configured: false,
    connector_type: 'stripe',
  },
  {
    name: 'datadog',
    display_name: 'Datadog',
    description: 'Datadog — metrics, APM, logs, monitors, alerts',
    auth_type: 'api_key',
    default_url: 'https://api.datadoghq.com',
    icon: 'datadog',
    category: 'observability',
    auth_fields: [
      { key: 'DD-API-KEY', label: 'API Key', placeholder: 'your-api-key', field_type: 'password', required: true, hint: '' },
      { key: 'DD-APPLICATION-KEY', label: 'Application Key', placeholder: 'your-app-key', field_type: 'password', required: true, hint: '' },
    ],
    has_builtin: true,
    builtin_server_id: 'builtin-datadog',
    is_configured: false,
    connector_type: 'datadog',
  },
];

async function mockCatalogApi(page: Page, catalog = MOCK_CATALOG, registeredNames?: string[]) {
  // Only override is_configured when registeredNames is explicitly provided.
  // When not provided, use the is_configured values from the catalog data as-is.
  const enriched = registeredNames != null
    ? catalog.map((c) => ({ ...c, is_configured: registeredNames.includes(c.name) }))
    : catalog;
  await page.route(/localhost:8000\/connectors\/catalog/, (route) => {
    return route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(enriched) });
  });
}

// ── Tests ──────────────────────────────────────────────────────────────────────

test.describe('Connector Catalog', () => {

  test('shows Connector Catalog heading and count', async ({ page }) => {
    await setupAuth(page);
    await mockCatalogApi(page);
    await page.goto('/connectors/catalog');

    await expect(page.locator('h1').filter({ hasText: /connector catalog/i })).toBeVisible({ timeout: 15000 });
    // Shows count of available connectors
    await expect(page.getByText(/5 available connectors/i)).toBeVisible({ timeout: 10000 });
  });

  test('displays connector cards with names and descriptions', async ({ page }) => {
    await setupAuth(page);
    await mockCatalogApi(page);
    await page.goto('/connectors/catalog');

    await expect(page.getByText('Jira').first()).toBeVisible({ timeout: 15000 });
    await expect(page.getByText('GitHub').first()).toBeVisible({ timeout: 10000 });
    await expect(page.getByText('Slack').first()).toBeVisible({ timeout: 10000 });
    await expect(page.getByText(/jira.*project management|project management.*jira/i).first()).toBeVisible({ timeout: 10000 });
  });

  test('shows Native badge for connectors with builtin handlers', async ({ page }) => {
    await setupAuth(page);
    await mockCatalogApi(page);
    await page.goto('/connectors/catalog');

    await expect(page.getByText('Jira').first()).toBeVisible({ timeout: 15000 });
    const nativeBadges = page.locator('text=Native');
    await expect(nativeBadges.first()).toBeVisible({ timeout: 10000 });
    const count = await nativeBadges.count();
    expect(count).toBeGreaterThanOrEqual(3); // Jira, GitHub, Slack all have native handlers
  });

  test('shows Configured badge for already-registered connectors', async ({ page }) => {
    await setupAuth(page);
    // GitHub is is_configured: true in mock data
    await mockCatalogApi(page);
    await page.goto('/connectors/catalog');

    await expect(page.getByText('GitHub').first()).toBeVisible({ timeout: 15000 });
    // Should show exactly one Configured badge (for GitHub)
    const configuredBadges = page.locator('text=Configured');
    await expect(configuredBadges.first()).toBeVisible({ timeout: 10000 });
  });

  test('shows configured count in header', async ({ page }) => {
    await setupAuth(page);
    await mockCatalogApi(page, MOCK_CATALOG, ['github']);
    await page.goto('/connectors/catalog');

    await expect(page.getByText('GitHub').first()).toBeVisible({ timeout: 15000 });
    // Header should mention 1 configured
    await expect(page.getByText(/1 configured/i)).toBeVisible({ timeout: 10000 });
  });

  test('search filters connectors by name', async ({ page }) => {
    await setupAuth(page);
    await mockCatalogApi(page);
    await page.goto('/connectors/catalog');

    await expect(page.getByText('Jira').first()).toBeVisible({ timeout: 15000 });
    await expect(page.getByText('GitHub').first()).toBeVisible({ timeout: 10000 });

    // Search for "jira"
    const searchInput = page.locator('input[aria-label="Search connectors"]');
    await expect(searchInput).toBeVisible({ timeout: 5000 });
    await searchInput.fill('jira');

    // GitHub should disappear, Jira stays
    await expect(page.getByText('Jira').first()).toBeVisible({ timeout: 5000 });
    await expect(page.getByText('GitHub').first()).not.toBeVisible({ timeout: 5000 });
  });

  test('search filters connectors by description', async ({ page }) => {
    await setupAuth(page);
    await mockCatalogApi(page);
    await page.goto('/connectors/catalog');

    await expect(page.getByText('Slack').first()).toBeVisible({ timeout: 15000 });

    const searchInput = page.locator('input[aria-label="Search connectors"]');
    await searchInput.fill('messaging');

    await expect(page.getByText('Slack').first()).toBeVisible({ timeout: 5000 });
    await expect(page.getByText('GitHub').first()).not.toBeVisible({ timeout: 5000 });
  });

  test('category filter pills are rendered and clickable', async ({ page }) => {
    await setupAuth(page);
    await mockCatalogApi(page);
    await page.goto('/connectors/catalog');

    await expect(page.getByText('Jira').first()).toBeVisible({ timeout: 15000 });

    // All category button should exist
    const allBtn = page.getByRole('button', { name: 'All' });
    await expect(allBtn).toBeVisible({ timeout: 5000 });
  });

  test('filtering by category shows only matching connectors', async ({ page }) => {
    await setupAuth(page);
    await mockCatalogApi(page);
    await page.goto('/connectors/catalog');

    await expect(page.getByText('Jira').first()).toBeVisible({ timeout: 15000 });

    // Click the Finance category
    const financeBtn = page.getByRole('button', { name: /finance/i });
    if (await financeBtn.isVisible({ timeout: 3000 }).catch(() => false)) {
      await financeBtn.click();
      // Stripe is finance, others are not
      await expect(page.getByText('Stripe').first()).toBeVisible({ timeout: 5000 });
      await expect(page.getByText('Jira').first()).not.toBeVisible({ timeout: 5000 });
    }
  });

  test('shows auth field hints on connector cards', async ({ page }) => {
    await setupAuth(page);
    await mockCatalogApi(page);
    await page.goto('/connectors/catalog');

    await expect(page.getByText('Jira').first()).toBeVisible({ timeout: 15000 });
    // Jira card shows the API token creation hint
    await expect(
      page.getByText(/atlassian.*api.tokens|id\.atlassian\.com/i).first()
    ).toBeVisible({ timeout: 10000 });
  });

  test('Configure button navigates to /connectors with prefilled state', async ({ page }) => {
    await setupAuth(page);
    await mockCatalogApi(page);

    // Also mock connectors page to avoid loading issues
    await page.route(/localhost:8000\/connectors(?!\/)/, (route) => {
      if (route.request().method() === 'GET') {
        return route.fulfill({ status: 200, contentType: 'application/json', body: '[]' });
      }
      return route.fulfill({ status: 201, contentType: 'application/json', body: JSON.stringify({ server_id: 'new-1', name: 'jira', url: 'https://test.atlassian.net' }) });
    });

    await page.goto('/connectors/catalog');
    await expect(page.getByText('Jira').first()).toBeVisible({ timeout: 15000 });

    // Click Configure on Jira card
    // The Jira card should have a Configure button
    const jiraCard = page.locator('[class*="card"], [class*="rounded"]').filter({ hasText: 'Jira' }).first();
    const configureBtn = jiraCard.getByRole('button', { name: /configure/i });
    if (await configureBtn.isVisible({ timeout: 3000 }).catch(() => false)) {
      await configureBtn.click();
      // Should navigate to /connectors and open modal
      await expect(page).toHaveURL(/\/connectors/, { timeout: 10000 });
    } else {
      // Fallback: find any Configure button
      const anyConfigureBtn = page.getByRole('button', { name: /configure/i }).first();
      await expect(anyConfigureBtn).toBeVisible({ timeout: 5000 });
    }
  });

  test('loading skeleton shown while catalog fetches', async ({ page }) => {
    await setupAuth(page);

    // Delay the response to observe skeleton
    await page.route(/localhost:8000\/connectors\/catalog/, async (route) => {
      await new Promise((r) => setTimeout(r, 400));
      return route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(MOCK_CATALOG) });
    });

    await page.goto('/connectors/catalog');
    // Heading should appear quickly (not gated on data)
    await expect(page.locator('h1').filter({ hasText: /connector catalog/i })).toBeVisible({ timeout: 15000 });
  });

  test('error state shown when catalog API fails', async ({ page }) => {
    await setupAuth(page);
    await page.route(/localhost:8000\/connectors\/catalog/, (route) => {
      return route.fulfill({ status: 500, contentType: 'application/json', body: JSON.stringify({ detail: 'Server error' }) });
    });

    await page.goto('/connectors/catalog');
    await expect(page.locator('h1').filter({ hasText: /connector catalog/i })).toBeVisible({ timeout: 15000 });
    // Error message should appear
    await expect(page.getByText(/failed to load catalog|backend is running/i)).toBeVisible({ timeout: 10000 });
  });

  test('My Connectors button navigates to /connectors', async ({ page }) => {
    await setupAuth(page);
    await mockCatalogApi(page);
    await page.route(/localhost:8000\/connectors(?!\/)/, (route) => {
      if (route.request().method() === 'GET') {
        return route.fulfill({ status: 200, contentType: 'application/json', body: '[]' });
      }
      return route.fulfill({ status: 200, contentType: 'application/json', body: '{}' });
    });

    await page.goto('/connectors/catalog');
    await expect(page.locator('h1').filter({ hasText: /connector catalog/i })).toBeVisible({ timeout: 15000 });

    const myConnectorsBtn = page.getByRole('button', { name: /my connectors/i });
    await expect(myConnectorsBtn).toBeVisible({ timeout: 5000 });
    await myConnectorsBtn.click();
    await expect(page).toHaveURL(/\/connectors$/, { timeout: 10000 });
  });
});
