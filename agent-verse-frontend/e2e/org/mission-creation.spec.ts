/**
 * E2E: Mission Creation — create mission via Command Bar and verify it starts.
 * Spec PART 39 (Command Bar) + PART 36 (Mission Page).
 */
import { test, expect, type Page } from '@playwright/test';

const BASE_URL = process.env.PLAYWRIGHT_BASE_URL ?? 'http://localhost:5173';
const ORG_ID   = process.env.TEST_ORG_ID ?? 'org-test-001';

test.skip(
  () => !process.env.TEST_ORG_ID && !process.env.E2E_FULL,
  'Set TEST_ORG_ID or E2E_FULL=1 to run org E2E tests',
);

test.describe('Mission Creation', () => {
  test.beforeEach(async ({ page }) => {
    await page.goto(`${BASE_URL}/org/${ORG_ID}`);
    await page.waitForLoadState('networkidle');
  });

  test('command bar opens on Cmd+K', async ({ page }) => {
    await page.keyboard.press('Meta+k');
    await expect(page.getByPlaceholder(/what do you want/i)).toBeVisible({ timeout: 5000 });
  });

  test('can type a goal in command bar', async ({ page }) => {
    await page.keyboard.press('Meta+k');
    const input = page.getByPlaceholder(/what do you want/i);
    await input.fill('Research our top 3 competitors');
    await expect(input).toHaveValue('Research our top 3 competitors');
  });

  test('command bar shows mission suggestion', async ({ page }) => {
    await page.keyboard.press('Meta+k');
    await page.getByPlaceholder(/what do you want/i).fill('Build a product');
    await expect(page.getByText(/create mission/i)).toBeVisible({ timeout: 3000 });
  });

  test('create mission via new button', async ({ page }) => {
    await page.getByRole('button', { name: /new mission/i }).first().click();
    await expect(page.getByRole('dialog')).toBeVisible({ timeout: 5000 });
  });

  test('mission appears in list after creation', async ({ page }) => {
    const uniqueTitle = `E2E Test Mission ${Date.now()}`;
    // Open create drawer
    await page.getByRole('button', { name: /new mission/i }).first().click();
    const dialog = page.getByRole('dialog');
    await expect(dialog).toBeVisible();
    // Fill mission
    await dialog.getByRole('textbox').first().fill(uniqueTitle);
    await dialog.getByRole('button', { name: /create|submit|launch/i }).click();
    // Mission appears in list
    await expect(page.getByText(uniqueTitle)).toBeVisible({ timeout: 10_000 });
  });

  test('keyboard: Escape closes command bar', async ({ page }) => {
    await page.keyboard.press('Meta+k');
    await page.waitForTimeout(300);
    await page.keyboard.press('Escape');
    await expect(page.getByPlaceholder(/what do you want/i)).not.toBeVisible();
  });
});
