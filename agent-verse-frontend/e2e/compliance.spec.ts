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

function mockComplianceApis(page: Page, {
  legalHolds = [] as object[],
  consent = [] as object[],
} = {}) {
  return Promise.all([
    page.route('**/governance/legal-holds', (route) =>
      route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify(legalHolds),
      })
    ),
    page.route('**/compliance/consent', (route) =>
      route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify(consent),
      })
    ),
    page.route(/localhost:8000\/compliance\/export/, (route) => {
      const url = route.request().url();
      if (url.includes('/jobs/')) {
        return route.fulfill({
          status: 200,
          contentType: 'application/json',
          body: JSON.stringify({
            job_id: 'job-001',
            status: 'complete',
            completed_at: new Date().toISOString(),
            download_url: 'https://example.com/export.zip',
            error: null,
          }),
        });
      }
      return route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({ job_id: 'job-001', status: 'pending', poll_url: '/compliance/export/jobs/job-001' }),
      });
    }),
  ]);
}

test.describe('Compliance', () => {
  test('shows Compliance h1 heading', async ({ page }) => {
    await setupAuth(page);
    await mockComplianceApis(page);
    await page.goto('/compliance');
    await expect(page.locator('h1').filter({ hasText: /compliance/i })).toBeVisible({
      timeout: 15000,
    });
  });

  test('shows "GDPR data export" section', async ({ page }) => {
    await setupAuth(page);
    await mockComplianceApis(page);
    await page.goto('/compliance');

    await expect(page.getByText('GDPR data export')).toBeVisible({ timeout: 15000 });
    await expect(page.getByRole('button', { name: /start gdpr export/i })).toBeVisible();
  });

  test('GDPR export button triggers export request', async ({ page }) => {
    let exportStarted = false;
    await setupAuth(page);
    await mockComplianceApis(page);

    await page.route('**/compliance/export/start', (route) => {
      exportStarted = true;
      return route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({ job_id: 'job-new', status: 'pending', poll_url: '/compliance/export/jobs/job-new' }),
      });
    });

    await page.goto('/compliance');
    await page.getByRole('button', { name: /start gdpr export/i }).click();

    // After click the button state changes (pending state or confirmation)
    await page.waitForTimeout(500);
    // Export was triggered
    expect(exportStarted).toBe(true);
  });

  test('shows empty state when no legal holds', async ({ page }) => {
    await setupAuth(page);
    await mockComplianceApis(page, { legalHolds: [] });
    await page.goto('/compliance');

    await expect(page.getByText('No active legal holds')).toBeVisible({ timeout: 15000 });
  });

  test('shows legal hold entries when present', async ({ page }) => {
    const holds = [
      {
        id: 'hold-001',
        reason: 'Litigation hold for Q4 case',
        expires_at: null,
        created_by: 'admin',
      },
    ];
    await setupAuth(page);
    await mockComplianceApis(page, { legalHolds: holds });
    await page.goto('/compliance');

    await expect(page.getByText('Litigation hold for Q4 case')).toBeVisible({ timeout: 15000 });
  });
});
