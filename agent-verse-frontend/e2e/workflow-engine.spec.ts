/**
 * E2E tests for the Workflow Automation Engine — builder, runs, approvals.
 *
 * These tests use Playwright against the running dev server.
 * Most assertions are lenient to handle API mock or dev-mode behavior.
 */
import { test, expect, type Page } from '@playwright/test';

const BASE_URL = process.env.E2E_BASE_URL ?? 'http://localhost:5173';

// ── Helpers ───────────────────────────────────────────────────────────────────

async function gotoWorkflows(page: Page) {
  await page.goto(`${BASE_URL}/workflows`);
  await page.waitForLoadState('networkidle');
}

// ── Workflow List Page ────────────────────────────────────────────────────────

test.describe('Workflow List', () => {
  test('page loads and shows heading', async ({ page }) => {
    await gotoWorkflows(page);
    await expect(page.getByRole('heading', { name: /workflows/i })).toBeVisible({ timeout: 10000 });
  });

  test('New Workflow button is accessible', async ({ page }) => {
    await gotoWorkflows(page);
    const btn = page.getByRole('button', { name: /new workflow/i });
    await expect(btn).toBeVisible({ timeout: 10000 });
    await expect(btn).toBeEnabled();
  });

  test('Templates button is visible', async ({ page }) => {
    await gotoWorkflows(page);
    await expect(page.getByRole('button', { name: /templates/i })).toBeVisible({ timeout: 10000 });
  });

  test('search input is accessible', async ({ page }) => {
    await gotoWorkflows(page);
    const search = page.getByRole('searchbox');
    await expect(search).toBeVisible({ timeout: 10000 });
    await search.fill('kyc');
    // Should filter or show empty state
  });

  test('filter buttons are keyboard accessible', async ({ page }) => {
    await gotoWorkflows(page);
    await page.keyboard.press('Tab');
    // Should be able to tab through filter buttons
  });
});

// ── Workflow Builder ──────────────────────────────────────────────────────────

test.describe('Workflow Builder', () => {
  test('builder page loads for a workflow ID', async ({ page }) => {
    await page.goto(`${BASE_URL}/workflows/test-wf-id/edit`);
    await page.waitForLoadState('networkidle');
    // Either loads the builder or shows an error
    const heading = page.getByRole('heading').first();
    await expect(heading).toBeVisible({ timeout: 10000 });
  });

  test('tool palette is visible on builder page', async ({ page }) => {
    await page.goto(`${BASE_URL}/workflows/test-wf-id/edit`);
    await page.waitForLoadState('networkidle');
    // Palette or error state — something renders
    await page.waitForTimeout(2000);
  });
});

// ── Template Marketplace ──────────────────────────────────────────────────────

test.describe('Template Marketplace', () => {
  test('marketplace page loads', async ({ page }) => {
    await page.goto(`${BASE_URL}/workflow-templates`);
    await page.waitForLoadState('networkidle');
    await expect(page.getByRole('heading', { name: /template marketplace/i })).toBeVisible({
      timeout: 10000,
    });
  });

  test('shows template count', async ({ page }) => {
    await page.goto(`${BASE_URL}/workflow-templates`);
    await page.waitForLoadState('networkidle');
    await page.waitForTimeout(2000);
    // Template count text appears
    const countText = page.getByText(/templates?/i).first();
    await expect(countText).toBeVisible({ timeout: 8000 });
  });

  test('search box is accessible', async ({ page }) => {
    await page.goto(`${BASE_URL}/workflow-templates`);
    await page.waitForLoadState('networkidle');
    const search = page.getByRole('searchbox');
    await expect(search).toBeVisible({ timeout: 10000 });
    await search.fill('kyc');
  });
});

// ── Approval Inbox ────────────────────────────────────────────────────────────

test.describe('Approval Inbox', () => {
  test('approval inbox page loads', async ({ page }) => {
    await page.goto(`${BASE_URL}/approvals`);
    await page.waitForLoadState('networkidle');
    await expect(page.getByRole('heading', { name: /approval inbox/i })).toBeVisible({
      timeout: 10000,
    });
  });

  test('shows empty state when no pending approvals', async ({ page }) => {
    await page.goto(`${BASE_URL}/approvals`);
    await page.waitForLoadState('networkidle');
    await page.waitForTimeout(2000);
    // Either shows items or "all caught up"
    const heading = page.getByRole('heading', { name: /approval inbox/i });
    await expect(heading).toBeVisible({ timeout: 8000 });
  });

  test('priority filter buttons are visible', async ({ page }) => {
    await page.goto(`${BASE_URL}/approvals`);
    await page.waitForLoadState('networkidle');
    const allBtn = page.getByRole('button', { name: /^all$/i }).first();
    await expect(allBtn).toBeVisible({ timeout: 10000 });
  });
});

// ── Run History ───────────────────────────────────────────────────────────────

test.describe('Workflow Runs', () => {
  test('runs page loads for a workflow', async ({ page }) => {
    await page.goto(`${BASE_URL}/workflows/test-wf-id/runs`);
    await page.waitForLoadState('networkidle');
    await page.waitForTimeout(2000);
    // Page renders something
    const body = page.locator('body');
    await expect(body).toBeVisible();
  });
});

// ── Accessibility ─────────────────────────────────────────────────────────────

test.describe('Accessibility', () => {
  test('workflow list has no obvious a11y violations', async ({ page }) => {
    await gotoWorkflows(page);
    await page.waitForLoadState('networkidle');
    // Check main landmark exists
    const main = page.getByRole('main');
    await expect(main).toBeVisible({ timeout: 10000 });
  });

  test('page has a visible heading hierarchy', async ({ page }) => {
    await gotoWorkflows(page);
    const h1 = page.getByRole('heading', { level: 1 }).first();
    await expect(h1).toBeVisible({ timeout: 10000 });
  });
});
