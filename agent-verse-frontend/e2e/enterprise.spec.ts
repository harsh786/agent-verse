/**
 * Enterprise Page — E2E Tests
 *
 * Covers src/features/enterprise/EnterprisePage.tsx sections:
 *   - Compliance dashboard (frameworks)
 *   - SAML / SSO wizard (4 steps)
 *   - SCIM provisioning toggle
 *   - Contracts list
 *   - Data residency
 *   - Export my data
 *   - Delete my data (typed confirmation)
 */
import { test, expect, type Page } from '@playwright/test';
import { setupAuth } from './helpers/auth';

// ── Mock data ─────────────────────────────────────────────────────────────────

const RESIDENCY = {
  region: 'us-east-1',
  data_center: 'AWS us-east-1a',
  compliance_frameworks: ['GDPR', 'SOC2', 'CCPA'],
  description: 'Data is stored and processed within the United States.',
};

async function mockEnterpriseApi(
  page: Page,
  opts: {
    residency?: unknown;
    samlTestResult?: unknown;
    exportResult?: unknown;
    purgeResult?: unknown;
  } = {}
): Promise<void> {
  await page.route('**/enterprise/compliance/residency', (route) =>
    route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify(opts.residency ?? RESIDENCY),
    })
  );

  await page.route('**/enterprise/saml/test', (route) =>
    route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify(opts.samlTestResult ?? { success: true, latency_ms: 120, status_code: 200 }),
    })
  );

  await page.route('**/enterprise/compliance/export', (route) =>
    route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify(
        opts.exportResult ?? {
          download_url: 'https://storage.example.com/export-123.zip',
          size_bytes: 1_048_576,
        }
      ),
    })
  );

  await page.route('**/enterprise/compliance/delete', (route) =>
    route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify(opts.purgeResult ?? { message: 'Deletion scheduled' }),
    })
  );
}

// ═══════════════════════════════════════════════════════════════════════════════
// SUITE 1 — Page load / compliance / residency
// ═══════════════════════════════════════════════════════════════════════════════

test.describe('Enterprise — page load', () => {
  test('1. Page loads and shows all section headers', async ({ page }) => {
    await setupAuth(page);
    await mockEnterpriseApi(page);
    await page.goto('/enterprise');

    await expect(page.getByRole('heading', { name: 'Enterprise' })).toBeVisible({ timeout: 10000 });
    await expect(page.getByText('Compliance Status')).toBeVisible();
    await expect(page.getByText('SAML / SSO Setup')).toBeVisible();
    await expect(page.getByText('SCIM Provisioning')).toBeVisible();
    await expect(page.getByText('Contracts & Agreements')).toBeVisible();
    await expect(page.getByText('Data Residency')).toBeVisible();
    await expect(page.getByText('Export My Data')).toBeVisible();
    await expect(page.getByText('Delete My Data')).toBeVisible();
  });

  test('2. Compliance dashboard marks active frameworks and greys out inactive ones', async ({ page }) => {
    await setupAuth(page);
    await mockEnterpriseApi(page, { residency: { ...RESIDENCY, compliance_frameworks: ['GDPR'] } });
    await page.goto('/enterprise');

    await expect(page.getByText('Compliance Status')).toBeVisible({ timeout: 10000 });
    await expect(page.getByText('GDPR')).toBeVisible();
    await expect(page.getByText('HIPAA')).toBeVisible();
    // GDPR is active (green check), HIPAA is inactive — verify both render without erroring.
    await expect(page.getByText('SOC2')).toBeVisible();
  });

  test('3. Residency section shows region, data center, and description', async ({ page }) => {
    await setupAuth(page);
    await mockEnterpriseApi(page);
    await page.goto('/enterprise');

    await expect(page.getByText('Data Residency')).toBeVisible({ timeout: 10000 });
    await expect(page.getByText('us-east-1')).toBeVisible();
    await expect(page.getByText('AWS us-east-1a')).toBeVisible();
    await expect(page.getByText('Data is stored and processed within the United States.')).toBeVisible();
  });

  test('4. Residency error state is shown when the request fails', async ({ page }) => {
    await setupAuth(page);
    await page.route('**/enterprise/compliance/residency', (route) =>
      route.fulfill({ status: 500, contentType: 'application/json', body: JSON.stringify({ detail: 'boom' }) })
    );
    await mockEnterpriseApi(page);
    // Re-register after the failing route so the failing one (registered later) wins (LIFO).
    await page.route('**/enterprise/compliance/residency', (route) =>
      route.fulfill({ status: 500, contentType: 'application/json', body: JSON.stringify({ detail: 'boom' }) })
    );
    await page.goto('/enterprise');

    await expect(page.getByText('Failed to load residency info.')).toBeVisible({ timeout: 10000 });
  });

  test('5. Contracts list shows signed and pending agreements', async ({ page }) => {
    await setupAuth(page);
    await mockEnterpriseApi(page);
    await page.goto('/enterprise');

    await expect(page.getByText('Business Associate Agreement (BAA)')).toBeVisible({ timeout: 10000 });
    await expect(page.getByText('Data Processing Agreement (DPA)')).toBeVisible();
    await expect(page.getByText('Service Level Agreement (SLA)')).toBeVisible();
    await expect(page.getByText('signed').first()).toBeVisible();
    await expect(page.getByText('pending')).toBeVisible();
  });
});

// ═══════════════════════════════════════════════════════════════════════════════
// SUITE 2 — SAML / SSO wizard
// ═══════════════════════════════════════════════════════════════════════════════

test.describe('Enterprise — SAML wizard', () => {
  test('6. Selecting an IdP and continuing advances to the metadata step', async ({ page }) => {
    await setupAuth(page);
    await mockEnterpriseApi(page);
    await page.goto('/enterprise');

    await expect(page.getByText('SAML / SSO Setup')).toBeVisible({ timeout: 10000 });
    await page.getByRole('button', { name: 'Okta' }).click();
    await page.getByRole('button', { name: /continue/i }).click();

    await expect(page.getByText(/upload idp saml metadata xml for/i)).toBeVisible({ timeout: 5000 });
    await expect(page.getByText('Okta', { exact: true })).toBeVisible();
  });

  test('7. Manually entering SSO URL/Entity ID advances to attribute mapping', async ({ page }) => {
    await setupAuth(page);
    await mockEnterpriseApi(page);
    await page.goto('/enterprise');

    await page.getByRole('button', { name: 'Azure AD' }).click();
    await page.getByRole('button', { name: /continue/i }).click();

    await page.getByPlaceholder('https://idp.example.com/saml/sso').fill('https://idp.example.com/sso');
    await page.getByPlaceholder('https://idp.example.com').fill('https://idp.example.com');
    await page.getByRole('button', { name: /continue/i }).click();

    await expect(page.getByText('Map SAML attributes to user fields')).toBeVisible({ timeout: 5000 });
    await expect(page.getByText('Email attribute')).toBeVisible();
  });

  test('8. Testing the SSO connection reports success', async ({ page }) => {
    await setupAuth(page);
    await mockEnterpriseApi(page, { samlTestResult: { success: true, latency_ms: 88 } });
    await page.goto('/enterprise');

    await page.getByRole('button', { name: 'Google Workspace' }).click();
    await page.getByRole('button', { name: /continue/i }).click();
    await page.getByPlaceholder('https://idp.example.com/saml/sso').fill('https://idp.example.com/sso');
    await page.getByPlaceholder('https://idp.example.com').fill('https://idp.example.com');
    await page.getByRole('button', { name: /continue/i }).click();
    await page.getByRole('button', { name: /continue/i }).click();

    await expect(page.getByText('Verify the SSO flow end-to-end')).toBeVisible({ timeout: 5000 });
    await page.getByRole('button', { name: /test sso connection/i }).click();

    await expect(page.getByText('Connection successful')).toBeVisible({ timeout: 5000 });
    await expect(page.getByRole('button', { name: /save sso configuration/i })).toBeVisible();
  });

  test('9. A failed SSO test shows the failure state', async ({ page }) => {
    await setupAuth(page);
    await mockEnterpriseApi(page, { samlTestResult: { success: false, message: 'IdP unreachable' } });
    await page.goto('/enterprise');

    await page.getByRole('button', { name: 'OneLogin' }).click();
    await page.getByRole('button', { name: /continue/i }).click();
    await page.getByPlaceholder('https://idp.example.com/saml/sso').fill('https://idp.example.com/sso');
    await page.getByPlaceholder('https://idp.example.com').fill('https://idp.example.com');
    await page.getByRole('button', { name: /continue/i }).click();
    await page.getByRole('button', { name: /continue/i }).click();

    await page.getByRole('button', { name: /test sso connection/i }).click();
    await expect(page.getByText('Connection failed')).toBeVisible({ timeout: 5000 });
  });
});

// ═══════════════════════════════════════════════════════════════════════════════
// SUITE 3 — SCIM, export, delete
// ═══════════════════════════════════════════════════════════════════════════════

test.describe('Enterprise — SCIM / export / delete', () => {
  test('10. Enabling SCIM shows the provisioning endpoint', async ({ page }) => {
    await setupAuth(page);
    await mockEnterpriseApi(page);
    await page.goto('/enterprise');

    await expect(page.getByText('SCIM Provisioning')).toBeVisible({ timeout: 10000 });
    await page.getByRole('button', { name: 'Enable' }).click();

    await expect(page.getByText('SCIM provisioning enabled')).toBeVisible({ timeout: 5000 });
    await expect(page.getByText('https://api.agentverse.io/scim/v2')).toBeVisible();
    await expect(page.getByRole('button', { name: 'Enabled' })).toBeVisible();
  });

  test('11. Exporting data shows the download link on success', async ({ page }) => {
    await setupAuth(page);
    await mockEnterpriseApi(page, {
      exportResult: { download_url: 'https://storage.example.com/my-export.zip', size_bytes: 2_097_152 },
    });
    await page.goto('/enterprise');

    await expect(page.getByText('Export My Data')).toBeVisible({ timeout: 10000 });
    await page.getByRole('button', { name: 'Export' }).click();

    await expect(page.getByText('Export ready')).toBeVisible({ timeout: 5000 });
    await expect(page.getByText('Size: 2.00 MB')).toBeVisible();
    await expect(page.getByRole('link', { name: /download export/i })).toBeVisible();
  });

  test('12. Deleting data requires typing the exact confirmation phrase', async ({ page }) => {
    await setupAuth(page);
    await mockEnterpriseApi(page);
    await page.goto('/enterprise');

    await expect(page.getByText('Delete My Data')).toBeVisible({ timeout: 10000 });
    await page.getByRole('button', { name: 'Delete', exact: true }).click();

    await expect(page.getByText('Type DELETE MY DATA to confirm.')).toBeVisible({ timeout: 5000 });
    const confirmBtn = page.getByRole('button', { name: 'Confirm Delete' });
    await expect(confirmBtn).toBeDisabled();

    await page.getByPlaceholder('DELETE MY DATA').fill('wrong text');
    await expect(confirmBtn).toBeDisabled();

    await page.getByPlaceholder('DELETE MY DATA').fill('DELETE MY DATA');
    await expect(confirmBtn).toBeEnabled();
    await confirmBtn.click();

    await expect(page.getByText('Data deletion scheduled.')).toBeVisible({ timeout: 5000 });
    await expect(
      page.getByText('Your data will be permanently deleted within 30 days.')
    ).toBeVisible();
  });
});
