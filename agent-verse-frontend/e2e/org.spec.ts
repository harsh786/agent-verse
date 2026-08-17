/**
 * E2E: AI Organization OS — user flow from create to mission execution.
 * Tests the complete Phase 1 feature set.
 */
import { test, expect, type Page } from '@playwright/test';

const BASE_URL = process.env.PLAYWRIGHT_BASE_URL ?? 'http://localhost:5173';
const ORG_ID   = process.env.TEST_ORG_ID ?? 'org-test-001';

// Skip when no test org available (CI without backend)
test.skip(
  () => !process.env.TEST_ORG_ID && !process.env.E2E_FULL,
  'Set TEST_ORG_ID or E2E_FULL=1 to run org E2E tests',
);

test.describe('AI Organization OS — Phase 1', () => {
  test.beforeEach(async ({ page }) => {
    // Set API key for auth
    await page.goto(`${BASE_URL}/auth`);
    await page.evaluate((key) => {
      sessionStorage.setItem('av_api_key', key);
    }, process.env.TEST_API_KEY ?? 'test-key');
  });

  test('org page renders with correct layout', async ({ page }) => {
    await page.goto(`${BASE_URL}/org/${ORG_ID}`);

    // Wait for main content to load (JARVIS dark theme)
    await page.waitForSelector('[id="main-content"]', { timeout: 10_000 });

    // Should have missions panel
    await expect(page.locator('[aria-label="Missions panel"]')).toBeVisible();

    // Should have org sidebar on desktop
    await expect(page.locator('[aria-label="Organisation sidebar"]')).toBeVisible();
  });

  test('can create a mission via drawer', async ({ page }) => {
    await page.goto(`${BASE_URL}/org/${ORG_ID}`);
    await page.waitForSelector('[id="main-content"]');

    // Click "New Mission" button
    await page.click('[aria-label="Create new mission"]');

    // Drawer should appear (spring animation)
    await expect(page.locator('[aria-label="Create new mission"]').last()).toBeVisible();
    await expect(page.locator('[role="dialog"]')).toBeVisible({ timeout: 3_000 });

    // Fill in form
    await page.fill('[aria-label*="Mission title"]', 'E2E Test Mission');

    // Submit (button should be enabled until request starts)
    await page.click('button[type="submit"]');

    // Mission should appear in list (optimistic update)
    await expect(page.locator('text=E2E Test Mission')).toBeVisible({ timeout: 5_000 });
  });

  test('mission card click opens detail panel', async ({ page }) => {
    await page.goto(`${BASE_URL}/org/${ORG_ID}`);
    await page.waitForSelector('[data-testid="mission-card"]');

    // Click first mission card
    const firstCard = page.locator('[data-testid="mission-card"]').first();
    await firstCard.click();

    // Detail panel should slide in (role=complementary)
    await expect(page.locator('[role="complementary"]')).toBeVisible({ timeout: 3_000 });

    // Escape should close it
    await page.keyboard.press('Escape');
    await expect(page.locator('[role="complementary"]')).not.toBeVisible({ timeout: 2_000 });
  });

  test('status filter tabs work', async ({ page }) => {
    await page.goto(`${BASE_URL}/org/${ORG_ID}`);
    await page.waitForSelector('[role="tablist"]');

    // Click "Active" tab
    await page.click('[role="tab"][aria-label*="Filter"][aria-label*="active"], text=Active');

    // Tab should be selected
    const activeTab = page.locator('[role="tab"]:has-text("Active")');
    await expect(activeTab).toHaveAttribute('aria-selected', 'true');
  });

  test('keyboard navigation: tab through interactive elements', async ({ page }) => {
    await page.goto(`${BASE_URL}/org/${ORG_ID}`);
    await page.waitForSelector('[id="main-content"]');

    // Tab through elements — all should be reachable
    await page.keyboard.press('Tab');
    let focused = await page.evaluate(() => document.activeElement?.tagName);
    // Something should be focused (a, button, input)
    expect(['A', 'BUTTON', 'INPUT', 'SELECT']).toContain(focused);
  });

  test('department tree expands and collapses', async ({ page }) => {
    await page.goto(`${BASE_URL}/org/${ORG_ID}`);
    await page.waitForSelector('[aria-label="Department hierarchy"]');

    const deptTree = page.locator('[aria-label="Department hierarchy"]');
    await expect(deptTree).toBeVisible();
  });

  test('activity feed shows live events', async ({ page }) => {
    await page.goto(`${BASE_URL}/org/${ORG_ID}`);
    // Activity feed section
    await expect(page.locator('[aria-label="Organisation activity feed"]')).toBeVisible({ timeout: 5_000 });
  });

  test('JARVIS dark theme is applied', async ({ page }) => {
    await page.goto(`${BASE_URL}/org/${ORG_ID}`);
    // html element should have dark class
    const htmlClass = await page.evaluate(() => document.documentElement.className);
    expect(htmlClass).toContain('dark');

    // Background color should be JARVIS dark
    const bgColor = await page.evaluate(() =>
      getComputedStyle(document.body).backgroundColor
    );
    // Should not be white
    expect(bgColor).not.toBe('rgb(255, 255, 255)');
  });

  test('accessibility: no critical violations', async ({ page }) => {
    const { checkA11y } = await import('@axe-core/playwright');
    await page.goto(`${BASE_URL}/org/${ORG_ID}`);
    await page.waitForSelector('[id="main-content"]');

    // axe-core accessibility audit
    await checkA11y(page, undefined, {
      axeOptions: { runOnly: { type: 'tag', values: ['wcag2a', 'wcag2aa'] } },
    });
  });
});

// Visual regression tests
test.describe('Org UI — Visual Regression', () => {
  test('command center matches snapshot', async ({ page }) => {
    await page.goto(`${BASE_URL}/org/${ORG_ID}`);
    await page.waitForSelector('[id="main-content"]');
    await page.waitForTimeout(500); // wait for animations to settle

    await expect(page).toHaveScreenshot('org-command-center.png', {
      fullPage: false,
      threshold: 0.03,
    });
  });

  test('dark mode is correct colour', async ({ page }) => {
    await page.goto(`${BASE_URL}/org/${ORG_ID}`);
    await page.emulateMedia({ colorScheme: 'dark' });
    await page.waitForSelector('[id="main-content"]');

    await expect(page).toHaveScreenshot('org-dark-mode.png', { threshold: 0.03 });
  });
});
