/**
 * Web Perception Lab — E2E Tests
 *
 * 14 tests across 5 suites:
 *   1. Provider status panel
 *   2. Single analysis — screenshot, analyze, extract
 *   3. Advanced options — full-page, CSS selector
 *   4. Batch analysis
 *   5. History tab
 */
import { test, expect, type Page } from '@playwright/test';

// ── Auth setup ────────────────────────────────────────────────────────────────

async function setupAuth(page: Page): Promise<void> {
  await page.route(/localhost:8000/, (route) =>
    route.fulfill({ status: 404, contentType: 'application/json', body: '{"detail":"not found"}' })
  );
  await page.addInitScript(() => {
    const AUTH = JSON.stringify({
      state: { apiKey: 'test-key', tenantId: 'test-tenant', plan: 'enterprise', isAuthenticated: true },
      version: 0,
    });
    localStorage.setItem('av-auth', AUTH);
    sessionStorage.setItem('av-auth', AUTH);
    localStorage.setItem('av_api_key', 'test-key');
  });
  await page.route('**/tenants/me', (route) =>
    route.fulfill({
      status: 200, contentType: 'application/json',
      body: JSON.stringify({ tenant_id: 'test-tenant', name: 'Test', plan: 'enterprise' }),
    })
  );
}

// ── Mock data ─────────────────────────────────────────────────────────────────

const SCREENSHOT_B64 =
  'iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNk+M9QDwADhgGAWjR9awAAAABJRU5ErkJggg=='; // 1×1 px PNG

const STATUS_OK = {
  playwright_available: true,
  vision_available: true,
  browser_actions: ['screenshot', 'extract_text'],
  image_formats: ['png'],
};

const STATUS_NO_PLAYWRIGHT = {
  playwright_available: false,
  vision_available: false,
  browser_actions: [],
  image_formats: [],
};

async function setupPerceptionRoutes(page: Page, opts: { playwright?: boolean } = {}): Promise<void> {
  const statusPayload = opts.playwright === false ? STATUS_NO_PLAYWRIGHT : STATUS_OK;

  await page.route('**/perception/status', (route) =>
    route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(statusPayload) })
  );

  await page.route('**/perception/screenshot', (route) =>
    route.fulfill({
      status: 200, contentType: 'application/json',
      body: JSON.stringify({ success: true, url: 'https://example.com', screenshot_b64: SCREENSHOT_B64, error: null }),
    })
  );

  await page.route('**/perception/analyze', (route) =>
    route.fulfill({
      status: 200, contentType: 'application/json',
      body: JSON.stringify({ analysis: 'This page shows a simple HTML demo with a heading and paragraph.', question: 'What is this page?', screenshot_provided: false }),
    })
  );

  await page.route('**/perception/extract', (route) =>
    route.fulfill({
      status: 200, contentType: 'application/json',
      body: JSON.stringify({ success: true, url: 'https://example.com', selector: 'body', text: 'Hello World\nThis is example text.', char_count: 38, error: null }),
    })
  );

  await page.route('**/perception/batch-analyze', (route) =>
    route.fulfill({
      status: 200, contentType: 'application/json',
      body: JSON.stringify({
        results: [
          { url: 'https://example.com', success: true, analysis: 'Example domain page.', screenshot_b64: SCREENSHOT_B64, text_content: 'Example Domain', error: null },
          { url: 'https://docs.example.com', success: true, analysis: 'Documentation page.', screenshot_b64: '', text_content: 'Docs', error: null },
        ],
        total: 2,
        succeeded: 2,
      }),
    })
  );

  await page.route('**/perception/goal-with-image', (route) =>
    route.fulfill({
      status: 202, contentType: 'application/json',
      body: JSON.stringify({ goal_id: 'goal-001', has_visual_context: true, original_goal: 'Analyze page' }),
    })
  );
}

// ═══════════════════════════════════════════════════════════════════════════════
// SUITE 1 — Provider Status Panel
// ═══════════════════════════════════════════════════════════════════════════════

test.describe('Perception — Provider Status', () => {

  test('1. Shows Browser (Playwright) and Vision LLM status badges', async ({ page }) => {
    await setupAuth(page);
    await setupPerceptionRoutes(page);
    await page.goto('/perception');

    await expect(page.getByText(/browser \(playwright\)/i)).toBeVisible({ timeout: 10000 });
    await expect(page.getByText(/vision llm/i)).toBeVisible({ timeout: 5000 });
    await expect(page.getByTestId('status-panel')).toBeVisible({ timeout: 5000 });
  });

  test('2. Shows install warning when Playwright is unavailable', async ({ page }) => {
    await setupAuth(page);
    await setupPerceptionRoutes(page, { playwright: false });
    await page.goto('/perception');

    await expect(page.getByText(/browser \(playwright\)/i)).toBeVisible({ timeout: 10000 });
    await expect(page.getByText(/playwright not installed|playwright is unavailable/i).first()).toBeVisible({ timeout: 8000 });
  });

  test('3. Action buttons are disabled when Playwright unavailable', async ({ page }) => {
    await setupAuth(page);
    await setupPerceptionRoutes(page, { playwright: false });
    await page.goto('/perception');

    await expect(page.getByText(/browser \(playwright\)/i)).toBeVisible({ timeout: 10000 });
    const screenshotBtn = page.getByTestId('btn-screenshot');
    // Button should be disabled OR not visible (playwrightOff === true)
    const isDisabled = await screenshotBtn.isDisabled({ timeout: 5000 }).catch(() => true);
    expect(isDisabled).toBe(true);
  });
});

// ═══════════════════════════════════════════════════════════════════════════════
// SUITE 2 — Single Analysis Tab
// ═══════════════════════════════════════════════════════════════════════════════

test.describe('Perception — Single Analysis', () => {

  test('4. URL input and all three action buttons are rendered', async ({ page }) => {
    await setupAuth(page);
    await setupPerceptionRoutes(page);
    await page.goto('/perception');

    await expect(page.getByText(/browser \(playwright\)/i)).toBeVisible({ timeout: 10000 });
    await expect(page.getByLabel(/^url$/i)).toBeVisible({ timeout: 5000 });
    await expect(page.getByTestId('btn-screenshot')).toBeVisible({ timeout: 5000 });
    await expect(page.getByTestId('btn-analyze')).toBeVisible({ timeout: 5000 });
    await expect(page.getByTestId('btn-extract')).toBeVisible({ timeout: 5000 });
  });

  test('5. Screenshot shows returned image with data URI', async ({ page }) => {
    await setupAuth(page);
    await setupPerceptionRoutes(page);
    await page.goto('/perception');

    await expect(page.getByText(/browser \(playwright\)/i)).toBeVisible({ timeout: 10000 });
    await page.getByLabel(/^url$/i).fill('https://example.com');
    await page.getByTestId('btn-screenshot').click();

    await expect(page.getByRole('img', { name: /screenshot/i })).toBeVisible({ timeout: 8000 });
    const src = await page.getByRole('img', { name: /screenshot/i }).getAttribute('src');
    expect(src).toContain('data:image/png;base64,');
    expect(src).toContain(SCREENSHOT_B64);
  });

  test('6. Analyze shows vision analysis result text', async ({ page }) => {
    await setupAuth(page);
    await setupPerceptionRoutes(page);
    await page.goto('/perception');

    await expect(page.getByText(/browser \(playwright\)/i)).toBeVisible({ timeout: 10000 });
    await page.getByLabel(/^url$/i).fill('https://example.com');
    await page.getByTestId('btn-analyze').click();

    await expect(
      page.getByText(/simple HTML demo|vision analysis/i).first()
    ).toBeVisible({ timeout: 10000 });
  });

  test('7. Extract text shows char count and content', async ({ page }) => {
    await setupAuth(page);
    await setupPerceptionRoutes(page);
    await page.goto('/perception');

    await expect(page.getByText(/browser \(playwright\)/i)).toBeVisible({ timeout: 10000 });
    await page.getByLabel(/^url$/i).fill('https://example.com');
    await page.getByTestId('btn-extract').click();

    await expect(page.getByText(/38.*chars|38 chars/i).first()).toBeVisible({ timeout: 8000 });
    await expect(page.getByText('Hello World')).toBeVisible({ timeout: 5000 });
  });

  test('8. "Create Goal from this page" button appears after analysis', async ({ page }) => {
    await setupAuth(page);
    await setupPerceptionRoutes(page);
    await page.goto('/perception');

    await expect(page.getByText(/browser \(playwright\)/i)).toBeVisible({ timeout: 10000 });
    await page.getByLabel(/^url$/i).fill('https://example.com');
    await page.getByTestId('btn-analyze').click();

    await expect(page.getByText(/simple HTML demo/i)).toBeVisible({ timeout: 10000 });
    await expect(page.getByText(/create goal from this page/i)).toBeVisible({ timeout: 5000 });
  });

  test('9. Copy button appears in analysis panel', async ({ page }) => {
    await setupAuth(page);
    await setupPerceptionRoutes(page);
    await page.goto('/perception');

    await expect(page.getByText(/browser \(playwright\)/i)).toBeVisible({ timeout: 10000 });
    await page.getByLabel(/^url$/i).fill('https://example.com');
    await page.getByTestId('btn-analyze').click();

    await expect(page.getByText(/simple HTML demo/i)).toBeVisible({ timeout: 10000 });
    await expect(page.getByText('Copy').first()).toBeVisible({ timeout: 5000 });
  });
});

// ═══════════════════════════════════════════════════════════════════════════════
// SUITE 3 — Advanced Options
// ═══════════════════════════════════════════════════════════════════════════════

test.describe('Perception — Advanced Options', () => {

  test('10. Advanced options panel toggles open on click', async ({ page }) => {
    await setupAuth(page);
    await setupPerceptionRoutes(page);
    await page.goto('/perception');

    await expect(page.getByText(/browser \(playwright\)/i)).toBeVisible({ timeout: 10000 });
    await page.getByText(/advanced options/i).click();

    await expect(page.getByLabel(/full.page screenshot/i)).toBeVisible({ timeout: 5000 });
    await expect(page.getByLabel(/css selector/i)).toBeVisible({ timeout: 5000 });
  });

  test('11. Full-page screenshot toggle changes checkbox state', async ({ page }) => {
    await setupAuth(page);
    await setupPerceptionRoutes(page);
    await page.goto('/perception');

    await expect(page.getByText(/browser \(playwright\)/i)).toBeVisible({ timeout: 10000 });
    await page.getByText(/advanced options/i).click();

    const toggle = page.getByLabel(/full.page screenshot/i);
    await expect(toggle).not.toBeChecked({ timeout: 5000 });
    await toggle.check();
    await expect(toggle).toBeChecked({ timeout: 3000 });
  });

  test('12. Custom CSS selector is used in extract request', async ({ page }) => {
    let capturedSelector = '';
    await setupAuth(page);
    await setupPerceptionRoutes(page);

    await page.route('**/perception/extract', (route) => {
      const body = JSON.parse(route.request().postData() ?? '{}');
      capturedSelector = body.selector ?? '';
      return route.fulfill({
        status: 200, contentType: 'application/json',
        body: JSON.stringify({ success: true, url: 'https://example.com', selector: body.selector, text: 'Nav content', char_count: 11, error: null }),
      });
    });

    await page.goto('/perception');
    await expect(page.getByText(/browser \(playwright\)/i)).toBeVisible({ timeout: 10000 });
    await page.getByText(/advanced options/i).click();

    const selectorInput = page.getByLabel(/css selector/i);
    await selectorInput.fill('nav');

    await page.getByLabel(/^url$/i).fill('https://example.com');
    await page.getByTestId('btn-extract').click();

    await expect(async () => {
      expect(capturedSelector).toBe('nav');
    }).toPass({ timeout: 5000 });
  });
});

// ═══════════════════════════════════════════════════════════════════════════════
// SUITE 4 — Batch Analysis
// ═══════════════════════════════════════════════════════════════════════════════

test.describe('Perception — Batch Analysis', () => {

  test('13. Batch tab shows textarea and Run Batch button', async ({ page }) => {
    await setupAuth(page);
    await setupPerceptionRoutes(page);
    await page.goto('/perception');

    await expect(page.getByText(/browser \(playwright\)/i)).toBeVisible({ timeout: 10000 });
    await page.getByText('Batch Analysis').click();

    await expect(page.getByLabel(/urls/i)).toBeVisible({ timeout: 5000 });
    await expect(page.getByTestId('btn-run-batch')).toBeVisible({ timeout: 5000 });
  });

  test('14. Batch analysis shows results for each URL', async ({ page }) => {
    await setupAuth(page);
    await setupPerceptionRoutes(page);
    await page.goto('/perception');

    await expect(page.getByText(/browser \(playwright\)/i)).toBeVisible({ timeout: 10000 });
    await page.getByText('Batch Analysis').click();

    const urlsTextarea = page.getByLabel(/urls/i);
    await urlsTextarea.fill('https://example.com\nhttps://docs.example.com');
    await page.getByTestId('btn-run-batch').click();

    await expect(page.getByText(/Example domain page\./i)).toBeVisible({ timeout: 10000 });
    await expect(page.getByText(/Documentation page\./i)).toBeVisible({ timeout: 5000 });
    await expect(page.getByText(/2\/2 succeeded/i)).toBeVisible({ timeout: 5000 });
  });
});

// ═══════════════════════════════════════════════════════════════════════════════
// SUITE 5 — History Tab
// ═══════════════════════════════════════════════════════════════════════════════

test.describe('Perception — History', () => {

  test('15. History tab shows empty state when no analyses done', async ({ page }) => {
    await setupAuth(page);
    await setupPerceptionRoutes(page);
    // Clear history before test
    await page.addInitScript(() => localStorage.removeItem('perception_history_v1'));
    await page.goto('/perception');

    await expect(page.getByText(/browser \(playwright\)/i)).toBeVisible({ timeout: 10000 });
    await page.getByText('History').click();

    await expect(page.getByText(/no analysis history yet/i)).toBeVisible({ timeout: 5000 });
  });

  test('16. History entry appears after running analysis', async ({ page }) => {
    await setupAuth(page);
    await setupPerceptionRoutes(page);
    await page.addInitScript(() => localStorage.removeItem('perception_history_v1'));
    await page.goto('/perception');

    await expect(page.getByText(/browser \(playwright\)/i)).toBeVisible({ timeout: 10000 });

    // Run an analysis to create history
    await page.getByLabel(/^url$/i).fill('https://example.com');
    await page.getByTestId('btn-analyze').click();
    await expect(page.getByText(/simple HTML demo/i)).toBeVisible({ timeout: 10000 });

    // Switch to History tab
    await page.getByText('History').click();
    await expect(page.getByText('https://example.com')).toBeVisible({ timeout: 5000 });
    await expect(page.getByText(/simple HTML demo/i).first()).toBeVisible({ timeout: 5000 });
  });
});
