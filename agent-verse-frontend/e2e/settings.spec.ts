import { test, expect, type Page } from '@playwright/test';

async function setupAuth(page: Page) {
  await page.addInitScript(() => {
    localStorage.setItem(
      'av-auth',
      JSON.stringify({
        state: {
          apiKey: 'test-key',
          tenantId: 'test-tenant',
          plan: 'free',
          isAuthenticated: true,
        },
        version: 0,
      })
    );
    localStorage.setItem('av_api_key', 'test-key');
  });
  await page.route('**/tenants/me', (route) =>
    route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({ tenant_id: 'test-tenant', name: 'Test Org', plan: 'free' }),
    })
  );
}

const MOCK_TENANT = {
  tenant_id: 'test-tenant',
  name: 'Test Org',
  plan: 'professional',
  created_at: '2024-01-01T00:00:00Z',
};

const MOCK_LLM_CONFIG = {
  provider: 'anthropic',
  model: 'claude-3-5-sonnet',
  api_key: 'sk-ant-hidden',
};

const MOCK_KEYS = [
  {
    key_id: 'key-001',
    name: 'Production key',
    created_at: '2024-01-15T10:00:00Z',
    last_used_at: '2024-06-01T08:00:00Z',
  },
];

test.describe('Settings', () => {
  test('shows Settings h1 heading', async ({ page }) => {
    await setupAuth(page);
    await page.route('**/tenants/me/keys', (route) =>
      route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify([]) })
    );
    await page.route('**/tenants/me/llm', (route) =>
      route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(MOCK_LLM_CONFIG) })
    );
    await page.goto('/settings');
    await expect(page.locator('h1').filter({ hasText: /settings/i })).toBeVisible({
      timeout: 15000,
    });
  });

  test('shows Profile section with tenant info', async ({ page }) => {
    await setupAuth(page);
    await page.route('**/tenants/me/keys', (route) =>
      route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify([]) })
    );
    await page.route('**/tenants/me/llm', (route) =>
      route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(MOCK_LLM_CONFIG) })
    );
    await page.goto('/settings');

    await expect(page.getByText('Profile')).toBeVisible({ timeout: 15000 });
    await expect(page.getByText(/profile, llm provider/i)).toBeVisible();
  });

  test('shows API Keys section', async ({ page }) => {
    await setupAuth(page);
    await page.route('**/tenants/me/keys', (route) =>
      route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify(MOCK_KEYS),
      })
    );
    await page.route('**/tenants/me/llm', (route) =>
      route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(MOCK_LLM_CONFIG) })
    );
    await page.goto('/settings');

    await expect(page.getByText('API Keys')).toBeVisible({ timeout: 15000 });
    await expect(page.getByText('Production key')).toBeVisible();
  });

  test('shows empty state when no API keys exist', async ({ page }) => {
    await setupAuth(page);
    await page.route('**/tenants/me/keys', (route) =>
      route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify([]) })
    );
    await page.route('**/tenants/me/llm', (route) =>
      route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(MOCK_LLM_CONFIG) })
    );
    await page.goto('/settings');

    await expect(page.getByText('No API keys. Create one above.')).toBeVisible({
      timeout: 15000,
    });
  });

  test('shows plan information in profile section', async ({ page }) => {
    await setupAuth(page);
    // Override tenants/me with a specific plan for this test
    await page.route(/localhost:8000\/tenants\/me$/, (route) =>
      route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify(MOCK_TENANT),
      })
    );
    await page.route('**/tenants/me/keys', (route) =>
      route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify([]) })
    );
    await page.route('**/tenants/me/llm', (route) =>
      route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(MOCK_LLM_CONFIG) })
    );
    await page.goto('/settings');

    await expect(page.getByText('Plan')).toBeVisible({ timeout: 15000 });
    await expect(page.getByText('professional')).toBeVisible();
  });
});
