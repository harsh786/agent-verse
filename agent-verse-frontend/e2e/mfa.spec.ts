/**
 * mfa.spec.ts — End-to-end tests for MFA login verification and settings management.
 *
 * Coverage:
 * - MFA Login Flow: /auth/mfa UI, TOTP/recovery inputs, auto-submit, error/success paths
 * - MFA Settings — Enrollment Wizard: Enable → Scan QR → Verify → Recovery Codes → Done
 * - MFA Settings — Disable Flow: confirmation input, cancel, successful disable
 * - MFA Settings — Recovery Code Regeneration: low-count warning, TOTP confirm, new codes
 *
 * All tests use the shared setupAuth helper so no real backend is required.
 * /auth/mfa is a public route (no RequireAuth); settings tests use setupAuth.
 */

import { test, expect, type Page } from '@playwright/test';
import { setupAuth } from './helpers/auth';

// ─── Shared mock data ─────────────────────────────────────────────────────────

const MOCK_MFA_STATUS_DISABLED = {
  enabled: false,
  has_pending_enrollment: false,
  recovery_codes_count: 0,
};

const MOCK_MFA_STATUS_ENABLED = {
  enabled: true,
  has_pending_enrollment: false,
  recovery_codes_count: 10,
};

const MOCK_ENROLL_RESPONSE = {
  secret: 'JBSWY3DPEHPK3PXP',
  provisioning_uri:
    'otpauth://totp/AgentVerse:test@example.com?secret=JBSWY3DPEHPK3PXP&issuer=AgentVerse',
  qr_code: null, // null → UI shows "QR code unavailable" placeholder
  account_name: 'test@example.com',
  issuer: 'AgentVerse',
  algorithm: 'SHA1',
  digits: 6,
  period: 30,
};

const MOCK_RECOVERY_CODES = [
  'ABC12-DEF34', 'GHI56-JKL78', 'MNO90-PQR12',
  'STU34-VWX56', 'YZA78-BCD90', 'EFG12-HIJ34',
  'KLM56-NOP78', 'QRS90-TUV12', 'WXY34-ZAB56',
  'CDE78-FGH90',
];

// ─── Shared route helpers ─────────────────────────────────────────────────────

async function mockMFAStatus(
  page: Page,
  status: typeof MOCK_MFA_STATUS_DISABLED | typeof MOCK_MFA_STATUS_ENABLED,
) {
  await page.route('**/auth/mfa/status', (route) =>
    route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify(status),
    }),
  );
}

async function mockEnroll(page: Page) {
  await page.route('**/auth/mfa/enroll', (route) =>
    route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify(MOCK_ENROLL_RESPONSE),
    }),
  );
}

async function mockVerifyEnrollSuccess(page: Page) {
  await page.route('**/auth/mfa/verify-enrollment', (route) =>
    route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({
        status: 'enabled',
        recovery_codes: MOCK_RECOVERY_CODES,
        message: 'MFA enabled!',
      }),
    }),
  );
}

async function mockVerifyEnrollFail(page: Page) {
  await page.route('**/auth/mfa/verify-enrollment', (route) =>
    route.fulfill({
      status: 422,
      contentType: 'application/json',
      body: JSON.stringify({ detail: 'Invalid TOTP code.' }),
    }),
  );
}

// ─────────────────────────────────────────────────────────────────────────────
// MFA Login Flow
// /auth/mfa is a public route — no setupAuth needed for these tests.
// ─────────────────────────────────────────────────────────────────────────────

test.describe('MFA Login Flow', () => {
  test('shows "Two-Factor Authentication" h1 heading', async ({ page }) => {
    await page.goto('/auth/mfa');
    await expect(
      page.locator('h1').filter({ hasText: 'Two-Factor Authentication' }),
    ).toBeVisible({ timeout: 8_000 });
  });

  test('shows TOTP input with placeholder "000000" and correct aria-label', async ({ page }) => {
    await page.goto('/auth/mfa');
    await expect(
      page.locator('input[placeholder="000000"][aria-label="TOTP verification code"]'),
    ).toBeVisible({ timeout: 8_000 });
  });

  test('shows "Use a recovery code instead" toggle button', async ({ page }) => {
    await page.goto('/auth/mfa');
    await expect(
      page.getByRole('button', { name: /use a recovery code instead/i }),
    ).toBeVisible({ timeout: 8_000 });
  });

  test('clicking toggle switches to XXXXX-XXXXX recovery code input', async ({ page }) => {
    await page.goto('/auth/mfa');
    await page.getByRole('button', { name: /use a recovery code instead/i }).click();
    await expect(
      page.locator('input[placeholder="XXXXX-XXXXX"][aria-label="Recovery code"]'),
    ).toBeVisible({ timeout: 3_000 });
    // Toggle text flips to "Use authenticator app instead"
    await expect(
      page.getByRole('button', { name: /use authenticator app instead/i }),
    ).toBeVisible();
  });

  test('toggling back to TOTP restores the 000000 input', async ({ page }) => {
    await page.goto('/auth/mfa');
    await page.getByRole('button', { name: /use a recovery code instead/i }).click();
    await page.getByRole('button', { name: /use authenticator app instead/i }).click();
    await expect(
      page.locator('input[placeholder="000000"][aria-label="TOTP verification code"]'),
    ).toBeVisible({ timeout: 3_000 });
  });

  test('Verify button is disabled when code is empty', async ({ page }) => {
    await page.goto('/auth/mfa');
    await expect(page.getByRole('button', { name: /^verify$/i })).toBeDisabled({
      timeout: 8_000,
    });
  });

  test('Verify button remains disabled when fewer than 6 TOTP digits are entered', async ({
    page,
  }) => {
    await page.goto('/auth/mfa');
    await page.locator('input[placeholder="000000"]').fill('12345'); // 5 digits
    await expect(page.getByRole('button', { name: /^verify$/i })).toBeDisabled();
  });

  test('invalid TOTP code triggers error toast after auto-submit', async ({ page }) => {
    await page.route('**/auth/mfa/verify', (route) =>
      route.fulfill({
        status: 422,
        contentType: 'application/json',
        body: JSON.stringify({ detail: 'Invalid TOTP code.' }),
      }),
    );
    await page.goto('/auth/mfa');
    // 6 digits triggers auto-submit (100ms setTimeout in handleCodeChange)
    await page.locator('input[placeholder="000000"]').fill('999999');
    await expect(page.getByText('Invalid code. Please try again.')).toBeVisible({
      timeout: 5_000,
    });
  });

  test('successful verification navigates to /goals', async ({ page }) => {
    await page.route('**/auth/mfa/verify', (route) =>
      route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({ status: 'verified', method: 'totp' }),
      }),
    );
    // Goals page prerequisites
    await page.route(/localhost:8000\/goals($|\?)/, (route) =>
      route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({ goals: [] }),
      }),
    );
    await page.route(/localhost:8000\/goals\/metrics/, (route) =>
      route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({
          active_goals: 0,
          total_goals: 0,
          success_rate: 0,
          avg_latency_ms: 0,
          cost_today_usd: 0,
          goals_today: 0,
        }),
      }),
    );
    await page.route('**/tenants/me', (route) =>
      route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({ tenant_id: 'tid', name: 'Test', plan: 'professional' }),
      }),
    );
    await page.goto('/auth/mfa');
    await page.locator('input[placeholder="000000"]').fill('123456'); // auto-submits
    await expect(page).toHaveURL(/goals/, { timeout: 8_000 });
  });

  test('recovery code Verify button enabled after 11-character code entered', async ({ page }) => {
    await page.goto('/auth/mfa');
    await page.getByRole('button', { name: /use a recovery code instead/i }).click();
    // Recovery code max length is 11 (e.g. "XXXXX-XXXXX")
    await page.locator('input[placeholder="XXXXX-XXXXX"]').fill('ABCDE-FGHIJ');
    await expect(page.getByRole('button', { name: /^verify$/i })).toBeEnabled({
      timeout: 2_000,
    });
  });

  test('low recovery codes warning appears after successful TOTP verify', async ({ page }) => {
    await page.route('**/auth/mfa/verify', (route) =>
      route.fulfill({
        status: 200,
        contentType: 'application/json',
        // 2 remaining triggers the low-codes warning toast
        body: JSON.stringify({ status: 'verified', method: 'totp', remaining_recovery_codes: 2 }),
      }),
    );
    await page.route(/localhost:8000\/goals/, (route) =>
      route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({ goals: [] }),
      }),
    );
    await page.route('**/tenants/me', (route) =>
      route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({ tenant_id: 'tid', name: 'Test', plan: 'professional' }),
      }),
    );
    await page.goto('/auth/mfa');
    await page.locator('input[placeholder="000000"]').fill('123456');
    // Warning toast fires before redirect
    await expect(page.getByText(/Only 2 recovery codes remaining/)).toBeVisible({
      timeout: 5_000,
    });
  });
});

// ─────────────────────────────────────────────────────────────────────────────
// MFA Settings — Enrollment Wizard
// Settings > Security tab uses ?tab=security param (useSearchParams in SettingsPage)
// ─────────────────────────────────────────────────────────────────────────────

test.describe('MFA Settings — Enrollment Wizard', () => {
  test.beforeEach(async ({ page }) => {
    await setupAuth(page);
    // /tenants/me/sessions used by SecurityTab — .catch(() => []) handles 404 gracefully,
    // so no explicit mock needed. Other tabs are not rendered when ?tab=security.
  });

  test('shows "MFA Disabled" status card on Security tab', async ({ page }) => {
    await mockMFAStatus(page, MOCK_MFA_STATUS_DISABLED);
    await page.goto('/settings?tab=security');
    await expect(page.getByText('MFA Disabled')).toBeVisible({ timeout: 10_000 });
    await expect(page.getByRole('button', { name: 'Enable MFA' })).toBeVisible();
  });

  test('shows "MFA Enabled" status card when MFA is active', async ({ page }) => {
    await mockMFAStatus(page, MOCK_MFA_STATUS_ENABLED);
    await page.goto('/settings?tab=security');
    await expect(page.getByText('MFA Enabled')).toBeVisible({ timeout: 10_000 });
    await expect(page.getByRole('button', { name: 'Disable MFA' }).first()).toBeVisible();
  });

  test('shows recovery_codes_count in the status description', async ({ page }) => {
    await mockMFAStatus(page, MOCK_MFA_STATUS_ENABLED);
    await page.goto('/settings?tab=security');
    await expect(page.getByText('10 recovery codes remaining')).toBeVisible({ timeout: 10_000 });
  });

  test('clicking "Enable MFA" starts enrollment and shows Step 1', async ({ page }) => {
    await mockMFAStatus(page, MOCK_MFA_STATUS_DISABLED);
    await mockEnroll(page);
    await page.goto('/settings?tab=security');
    await page.getByRole('button', { name: 'Enable MFA' }).click();
    await expect(page.getByText('Step 1: Scan QR Code')).toBeVisible({ timeout: 5_000 });
  });

  test('Step 1 shows QR unavailable placeholder when qr_code is null', async ({ page }) => {
    await mockMFAStatus(page, MOCK_MFA_STATUS_DISABLED);
    await mockEnroll(page);
    await page.goto('/settings?tab=security');
    await page.getByRole('button', { name: 'Enable MFA' }).click();
    await expect(
      page.getByText('QR code unavailable. Use the manual key below.'),
    ).toBeVisible({ timeout: 5_000 });
  });

  test('Step 1 shows "Manual entry key" section', async ({ page }) => {
    await mockMFAStatus(page, MOCK_MFA_STATUS_DISABLED);
    await mockEnroll(page);
    await page.goto('/settings?tab=security');
    await page.getByRole('button', { name: 'Enable MFA' }).click();
    await expect(page.getByText('Manual entry key')).toBeVisible({ timeout: 5_000 });
  });

  test('secret key is blurred by default and "Show secret" button reveals it', async ({
    page,
  }) => {
    await mockMFAStatus(page, MOCK_MFA_STATUS_DISABLED);
    await mockEnroll(page);
    await page.goto('/settings?tab=security');
    await page.getByRole('button', { name: 'Enable MFA' }).click();
    await page.getByText('Manual entry key').waitFor({ timeout: 5_000 });
    // Default state: aria-label is "Show secret" (eye icon, secret blurred)
    const showBtn = page.getByRole('button', { name: 'Show secret' });
    await expect(showBtn).toBeVisible();
    await showBtn.click();
    // After click: aria-label flips to "Hide secret"
    await expect(page.getByRole('button', { name: 'Hide secret' })).toBeVisible({
      timeout: 2_000,
    });
  });

  test('clicking "I\'ve scanned the QR code" advances to Step 2', async ({ page }) => {
    await mockMFAStatus(page, MOCK_MFA_STATUS_DISABLED);
    await mockEnroll(page);
    await page.goto('/settings?tab=security');
    await page.getByRole('button', { name: 'Enable MFA' }).click();
    await page.getByText('Step 1: Scan QR Code').waitFor({ timeout: 5_000 });
    // Button text: "I've scanned the QR code →" (I&apos;ve scanned the QR code &rarr;)
    await page.getByRole('button', { name: /i've scanned/i }).click();
    await expect(page.getByText('Step 2: Verify your code')).toBeVisible({ timeout: 3_000 });
  });

  test('Step 2 shows numeric input with placeholder "000000" and aria-label "TOTP code"', async ({
    page,
  }) => {
    await mockMFAStatus(page, MOCK_MFA_STATUS_DISABLED);
    await mockEnroll(page);
    await page.goto('/settings?tab=security');
    await page.getByRole('button', { name: 'Enable MFA' }).click();
    await page.getByRole('button', { name: /i've scanned/i }).click();
    await expect(
      page.locator('input[placeholder="000000"][aria-label="TOTP code"]'),
    ).toBeVisible({ timeout: 3_000 });
  });

  test('"Verify & Enable" button is disabled until all 6 digits are entered', async ({ page }) => {
    await mockMFAStatus(page, MOCK_MFA_STATUS_DISABLED);
    await mockEnroll(page);
    await page.goto('/settings?tab=security');
    await page.getByRole('button', { name: 'Enable MFA' }).click();
    await page.getByRole('button', { name: /i've scanned/i }).click();
    const verifyBtn = page.getByRole('button', { name: 'Verify & Enable' });
    await expect(verifyBtn).toBeDisabled({ timeout: 3_000 });
    await page.locator('input[aria-label="TOTP code"]').fill('12345'); // 5 digits
    await expect(verifyBtn).toBeDisabled();
    await page.locator('input[aria-label="TOTP code"]').fill('123456'); // 6 digits
    await expect(verifyBtn).toBeEnabled({ timeout: 2_000 });
  });

  test('incorrect TOTP on enrollment shows error toast', async ({ page }) => {
    await mockMFAStatus(page, MOCK_MFA_STATUS_DISABLED);
    await mockEnroll(page);
    await mockVerifyEnrollFail(page);
    await page.goto('/settings?tab=security');
    await page.getByRole('button', { name: 'Enable MFA' }).click();
    await page.getByRole('button', { name: /i've scanned/i }).click();
    await page.locator('input[aria-label="TOTP code"]').fill('000000');
    // auto-submit fires at 6 digits; or click the button if still visible
    const verifyBtn = page.getByRole('button', { name: 'Verify & Enable' });
    if (await verifyBtn.isVisible({ timeout: 500 }).catch(() => false)) {
      await verifyBtn.click();
    }
    await expect(
      page.getByText('Invalid code — check your authenticator app'),
    ).toBeVisible({ timeout: 5_000 });
  });

  test('successful enrollment shows recovery codes panel', async ({ page }) => {
    await mockMFAStatus(page, MOCK_MFA_STATUS_DISABLED);
    await mockEnroll(page);
    await mockVerifyEnrollSuccess(page);
    await page.goto('/settings?tab=security');
    await page.getByRole('button', { name: 'Enable MFA' }).click();
    await page.getByRole('button', { name: /i've scanned/i }).click();
    await page.locator('input[aria-label="TOTP code"]').fill('123456');
    // Heading: "MFA Enabled — Save Your Recovery Codes" (Done &mdash; I&apos;ve saved…)
    await expect(
      page.getByText('MFA Enabled — Save Your Recovery Codes'),
    ).toBeVisible({ timeout: 5_000 });
    // First recovery code is visible
    await expect(page.getByText('ABC12-DEF34')).toBeVisible();
  });

  test('all 10 recovery codes are displayed after successful enrollment', async ({ page }) => {
    await mockMFAStatus(page, MOCK_MFA_STATUS_DISABLED);
    await mockEnroll(page);
    await mockVerifyEnrollSuccess(page);
    await page.goto('/settings?tab=security');
    await page.getByRole('button', { name: 'Enable MFA' }).click();
    await page.getByRole('button', { name: /i've scanned/i }).click();
    await page.locator('input[aria-label="TOTP code"]').fill('123456');
    await page.getByText('MFA Enabled — Save Your Recovery Codes').waitFor({ timeout: 5_000 });
    for (const code of MOCK_RECOVERY_CODES) {
      await expect(page.getByText(code)).toBeVisible();
    }
  });

  test('"Copy all codes" shows success toast "All recovery codes copied!"', async ({ page }) => {
    await mockMFAStatus(page, MOCK_MFA_STATUS_DISABLED);
    await mockEnroll(page);
    await mockVerifyEnrollSuccess(page);
    await page.goto('/settings?tab=security');
    await page.getByRole('button', { name: 'Enable MFA' }).click();
    await page.getByRole('button', { name: /i've scanned/i }).click();
    await page.locator('input[aria-label="TOTP code"]').fill('123456');
    await page.getByText('MFA Enabled — Save Your Recovery Codes').waitFor({ timeout: 5_000 });
    await page.getByRole('button', { name: 'Copy all codes' }).click();
    await expect(page.getByText('All recovery codes copied!')).toBeVisible({ timeout: 3_000 });
  });

  test('"Done" button closes recovery codes panel and returns to status card', async ({
    page,
  }) => {
    // After enrollment the status query is re-fetched — return enabled on 2nd+ call
    let statusCallCount = 0;
    await page.route('**/auth/mfa/status', (route) => {
      statusCallCount++;
      return route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify(
          statusCallCount <= 1 ? MOCK_MFA_STATUS_DISABLED : MOCK_MFA_STATUS_ENABLED,
        ),
      });
    });
    await mockEnroll(page);
    await mockVerifyEnrollSuccess(page);
    await page.goto('/settings?tab=security');
    await page.getByRole('button', { name: 'Enable MFA' }).click();
    await page.getByRole('button', { name: /i've scanned/i }).click();
    await page.locator('input[aria-label="TOTP code"]').fill('123456');
    await page.getByText('MFA Enabled — Save Your Recovery Codes').waitFor({ timeout: 5_000 });
    // Button text: "Done — I've saved my recovery codes" (Done &mdash; I&apos;ve saved my recovery codes)
    await page.getByRole('button', { name: /Done/i }).click();
    await expect(
      page.getByText('MFA Enabled — Save Your Recovery Codes'),
    ).not.toBeVisible({ timeout: 3_000 });
    await expect(page.getByText('MFA Enabled')).toBeVisible({ timeout: 8_000 });
  });
});

// ─────────────────────────────────────────────────────────────────────────────
// MFA Settings — Disable Flow
// ─────────────────────────────────────────────────────────────────────────────

test.describe('MFA Settings — Disable Flow', () => {
  test.beforeEach(async ({ page }) => {
    await setupAuth(page);
    await mockMFAStatus(page, MOCK_MFA_STATUS_ENABLED);
  });

  test('clicking "Disable MFA" opens the disable confirmation panel', async ({ page }) => {
    await page.goto('/settings?tab=security');
    await page.getByText('MFA Enabled').waitFor({ timeout: 10_000 });
    await page.getByRole('button', { name: 'Disable MFA' }).first().click();
    await expect(
      page.getByText('Enter your current TOTP code to disable MFA.'),
    ).toBeVisible({ timeout: 5_000 });
    await expect(
      page.locator('input[placeholder="000000"][aria-label="TOTP code to disable MFA"]'),
    ).toBeVisible();
  });

  test('Disable confirm button is disabled until 6 digits are entered', async ({ page }) => {
    await page.goto('/settings?tab=security');
    await page.getByText('MFA Enabled').waitFor({ timeout: 10_000 });
    await page.getByRole('button', { name: 'Disable MFA' }).first().click();
    const confirmBtn = page.getByRole('button', { name: 'Disable MFA' }).last();
    await expect(confirmBtn).toBeDisabled({ timeout: 3_000 });
    await page.locator('input[aria-label="TOTP code to disable MFA"]').fill('12345');
    await expect(confirmBtn).toBeDisabled();
    await page.locator('input[aria-label="TOTP code to disable MFA"]').fill('123456');
    await expect(confirmBtn).toBeEnabled({ timeout: 2_000 });
  });

  test('Cancel button restores the MFA Enabled status card', async ({ page }) => {
    await page.goto('/settings?tab=security');
    await page.getByText('MFA Enabled').waitFor({ timeout: 10_000 });
    await page.getByRole('button', { name: 'Disable MFA' }).first().click();
    await page
      .getByText('Enter your current TOTP code to disable MFA.')
      .waitFor({ timeout: 5_000 });
    await page.getByRole('button', { name: 'Cancel' }).click();
    await expect(
      page.getByText('Enter your current TOTP code to disable MFA.'),
    ).not.toBeVisible();
    await expect(page.getByText('MFA Enabled')).toBeVisible();
  });

  test('successful disable shows "MFA disabled." toast and "MFA Disabled" status', async ({
    page,
  }) => {
    await page.route('**/auth/mfa/disable', (route) =>
      route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({ status: 'disabled', message: 'MFA has been disabled.' }),
      }),
    );
    // After disable, status query is re-fetched — return disabled on 2nd+ call
    let disableCallCount = 0;
    await page.route('**/auth/mfa/status', (route) => {
      disableCallCount++;
      return route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify(
          disableCallCount <= 1 ? MOCK_MFA_STATUS_ENABLED : MOCK_MFA_STATUS_DISABLED,
        ),
      });
    });
    await page.goto('/settings?tab=security');
    await page.getByText('MFA Enabled').waitFor({ timeout: 10_000 });
    await page.getByRole('button', { name: 'Disable MFA' }).first().click();
    await page.locator('input[aria-label="TOTP code to disable MFA"]').fill('123456');
    await page.getByRole('button', { name: 'Disable MFA' }).last().click();
    await expect(page.getByText('MFA disabled.')).toBeVisible({ timeout: 5_000 });
    await expect(page.getByText('MFA Disabled')).toBeVisible({ timeout: 8_000 });
  });

  test('invalid code on disable confirmation shows "Invalid code" error toast', async ({
    page,
  }) => {
    await page.route('**/auth/mfa/disable', (route) =>
      route.fulfill({
        status: 422,
        contentType: 'application/json',
        body: JSON.stringify({ detail: 'Invalid TOTP code.' }),
      }),
    );
    await page.goto('/settings?tab=security');
    await page.getByText('MFA Enabled').waitFor({ timeout: 10_000 });
    await page.getByRole('button', { name: 'Disable MFA' }).first().click();
    await page.locator('input[aria-label="TOTP code to disable MFA"]').fill('000000');
    await page.getByRole('button', { name: 'Disable MFA' }).last().click();
    await expect(page.getByText('Invalid code')).toBeVisible({ timeout: 5_000 });
  });
});

// ─────────────────────────────────────────────────────────────────────────────
// MFA Settings — Recovery Code Regeneration
// ─────────────────────────────────────────────────────────────────────────────

test.describe('MFA Settings — Recovery Code Regeneration', () => {
  test.beforeEach(async ({ page }) => {
    await setupAuth(page);
  });

  test('shows amber warning when recovery_codes_count is ≤ 3', async ({ page }) => {
    await page.route('**/auth/mfa/status', (route) =>
      route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({
          enabled: true,
          has_pending_enrollment: false,
          recovery_codes_count: 2,
        }),
      }),
    );
    await page.goto('/settings?tab=security');
    // Warning text: "Only 2 recovery codes remaining. Regenerate them before you run out."
    await expect(page.getByText(/Only 2 recovery codes remaining/)).toBeVisible({
      timeout: 10_000,
    });
    await expect(page.getByText(/Regenerate them before you run out/)).toBeVisible();
  });

  test('no warning shown when recovery_codes_count is > 3', async ({ page }) => {
    await mockMFAStatus(page, MOCK_MFA_STATUS_ENABLED); // count = 10
    await page.goto('/settings?tab=security');
    await page.getByText('MFA Enabled').waitFor({ timeout: 10_000 });
    await expect(page.getByText(/Regenerate them before you run out/)).not.toBeVisible();
  });

  test('clicking "Regenerate codes" opens the regeneration panel', async ({ page }) => {
    await mockMFAStatus(page, MOCK_MFA_STATUS_ENABLED);
    await page.goto('/settings?tab=security');
    await page.getByText('MFA Enabled').waitFor({ timeout: 10_000 });
    await page.getByRole('button', { name: 'Regenerate codes' }).click();
    await expect(page.getByText('Regenerate Recovery Codes')).toBeVisible({ timeout: 5_000 });
    await expect(
      page.locator(
        'input[placeholder="000000"][aria-label="TOTP code to regenerate recovery codes"]',
      ),
    ).toBeVisible();
  });

  test('Regenerate button is disabled until 6 digits entered', async ({ page }) => {
    await mockMFAStatus(page, MOCK_MFA_STATUS_ENABLED);
    await page.goto('/settings?tab=security');
    await page.getByRole('button', { name: 'Regenerate codes' }).click();
    const regenBtn = page.getByRole('button', { name: 'Regenerate' }).last();
    await expect(regenBtn).toBeDisabled({ timeout: 3_000 });
    await page
      .locator('input[aria-label="TOTP code to regenerate recovery codes"]')
      .fill('12345');
    await expect(regenBtn).toBeDisabled();
    await page
      .locator('input[aria-label="TOTP code to regenerate recovery codes"]')
      .fill('123456');
    await expect(regenBtn).toBeEnabled({ timeout: 2_000 });
  });

  test('Cancel from regeneration panel restores the status card', async ({ page }) => {
    await mockMFAStatus(page, MOCK_MFA_STATUS_ENABLED);
    await page.goto('/settings?tab=security');
    await page.getByRole('button', { name: 'Regenerate codes' }).click();
    await page.getByText('Regenerate Recovery Codes').waitFor({ timeout: 5_000 });
    await page.getByRole('button', { name: 'Cancel' }).click();
    await expect(page.getByText('Regenerate Recovery Codes')).not.toBeVisible();
    await expect(page.getByText('MFA Enabled')).toBeVisible();
  });

  test('successful regeneration shows new codes and success toast', async ({ page }) => {
    const NEW_CODES = Array.from(
      { length: 10 },
      (_, i) => `NEW${String(i).padStart(2, '0')}-ABCD`,
    );
    await mockMFAStatus(page, MOCK_MFA_STATUS_ENABLED);
    await page.route('**/auth/mfa/regenerate', (route) =>
      route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({ recovery_codes: NEW_CODES, message: 'Regenerated.' }),
      }),
    );
    await page.goto('/settings?tab=security');
    await page.getByRole('button', { name: 'Regenerate codes' }).click();
    await page
      .locator('input[aria-label="TOTP code to regenerate recovery codes"]')
      .fill('123456');
    await page.getByRole('button', { name: 'Regenerate' }).last().click();
    await expect(page.getByText('NEW00-ABCD')).toBeVisible({ timeout: 5_000 });
    await expect(page.getByText('Recovery codes regenerated.')).toBeVisible({ timeout: 3_000 });
  });

  test('all 10 new codes are shown in the recovery codes panel after regen', async ({ page }) => {
    const NEW_CODES = Array.from(
      { length: 10 },
      (_, i) => `NEW${String(i).padStart(2, '0')}-ABCD`,
    );
    await mockMFAStatus(page, MOCK_MFA_STATUS_ENABLED);
    await page.route('**/auth/mfa/regenerate', (route) =>
      route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({ recovery_codes: NEW_CODES, message: 'Regenerated.' }),
      }),
    );
    await page.goto('/settings?tab=security');
    await page.getByRole('button', { name: 'Regenerate codes' }).click();
    await page
      .locator('input[aria-label="TOTP code to regenerate recovery codes"]')
      .fill('123456');
    await page.getByRole('button', { name: 'Regenerate' }).last().click();
    await page.getByText('NEW00-ABCD').waitFor({ timeout: 5_000 });
    for (const code of NEW_CODES) {
      await expect(page.getByText(code)).toBeVisible();
    }
  });

  test('invalid code on regeneration shows "Invalid code" error toast', async ({ page }) => {
    await mockMFAStatus(page, MOCK_MFA_STATUS_ENABLED);
    await page.route('**/auth/mfa/regenerate', (route) =>
      route.fulfill({
        status: 422,
        contentType: 'application/json',
        body: JSON.stringify({ detail: 'Invalid TOTP code.' }),
      }),
    );
    await page.goto('/settings?tab=security');
    await page.getByRole('button', { name: 'Regenerate codes' }).click();
    await page
      .locator('input[aria-label="TOTP code to regenerate recovery codes"]')
      .fill('000000');
    await page.getByRole('button', { name: 'Regenerate' }).last().click();
    await expect(page.getByText('Invalid code')).toBeVisible({ timeout: 5_000 });
  });
});
