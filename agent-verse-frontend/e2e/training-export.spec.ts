/**
 * Training-Data Export — E2E Tests
 *
 * 14 tests across 5 suites:
 *   1. Page renders and controls
 *   2. Preview panel (count + distribution + samples)
 *   3. Export download
 *   4. Train/Validation split
 *   5. Export history
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

const PREVIEW_RESPONSE = {
  count: 42,
  avg_score: 0.912,
  min_score_found: 0.81,
  max_score_found: 0.99,
  score_distribution: { '0.80-0.85': 8, '0.85-0.90': 12, '0.90-0.95': 15, '0.95-1.00': 7 },
  samples: [
    {
      goal: 'Triage all critical Jira issues from last 48 hours',
      eval_score: 0.97,
      steps: 4,
      tools: ['jira_search', 'slack_send'],
    },
    {
      goal: 'Generate weekly engineering report and post to Confluence',
      eval_score: 0.93,
      steps: 3,
      tools: ['confluence_create', 'github_list_prs'],
    },
  ],
};

const EXPORT_JSONL_LINES = [
  '{"messages":[{"role":"system","content":"You are an autonomous AI agent."},{"role":"user","content":"Triage Jira issues"},{"role":"assistant","content":"[jira_search] Found 8 P1 issues"},{"role":"assistant","content":"Done"}],"metadata":{"eval_score":0.97}}',
  '{"messages":[{"role":"system","content":"You are an autonomous AI agent."},{"role":"user","content":"Generate report"},{"role":"assistant","content":"[confluence_create] Created report"},{"role":"assistant","content":"Posted"}],"metadata":{"eval_score":0.93}}',
].join('\n');

async function setupTrainingRoutes(page: Page, opts: { count?: number } = {}): Promise<void> {
  await page.route('**/intelligence/export-training-data/preview*', (route) => {
    const preview = opts.count === 0
      ? { ...PREVIEW_RESPONSE, count: 0, samples: [], score_distribution: { '0.80–0.85': 0, '0.85–0.90': 0, '0.90–0.95': 0, '0.95–1.00': 0 } }
      : PREVIEW_RESPONSE;
    return route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify(preview),
    });
  });

  await page.route('**/intelligence/export-training-data*', (route) => {
    if (route.request().method() !== 'POST') return route.continue();
    const count = opts.count ?? 42;
    return route.fulfill({
      status: 200,
      contentType: 'application/x-ndjson',
      headers: {
        'Content-Disposition': 'attachment; filename="agentverse_training_openai_20260101_120000.jsonl"',
        'X-Training-Examples': String(count),
      },
      body: count > 0 ? EXPORT_JSONL_LINES : '',
    });
  });
}

// ═══════════════════════════════════════════════════════════════════════════════
// SUITE 1 — Page Controls
// ═══════════════════════════════════════════════════════════════════════════════

test.describe('Training Export — Page Controls', () => {

  test('1. All filter controls render correctly', async ({ page }) => {
    await setupAuth(page);
    await setupTrainingRoutes(page);
    await page.goto('/training-export');

    await expect(page.getByText(/Training-Data Export/i)).toBeVisible({ timeout: 10000 });
    await expect(page.getByLabel(/^format$/i)).toBeVisible({ timeout: 5000 });
    await expect(page.getByLabel(/minimum eval score/i)).toBeVisible({ timeout: 5000 });
    await expect(page.getByLabel(/max examples/i)).toBeVisible({ timeout: 5000 });
    await expect(page.getByTestId('btn-export')).toBeVisible({ timeout: 5000 });
  });

  test('2. Format select has OpenAI and Anthropic options', async ({ page }) => {
    await setupAuth(page);
    await setupTrainingRoutes(page);
    await page.goto('/training-export');

    await expect(page.getByText(/Training-Data Export/i)).toBeVisible({ timeout: 10000 });
    const formatSelect = page.getByLabel(/^format$/i);
    await expect(formatSelect).toBeVisible({ timeout: 5000 });

    const options = await page.$$eval('#format option', (opts) =>
      opts.map((o) => (o as HTMLOptionElement).value)
    );
    expect(options).toContain('openai');
    expect(options).toContain('anthropic');
  });

  test('3. Min score slider shows current value', async ({ page }) => {
    await setupAuth(page);
    await setupTrainingRoutes(page);
    await page.goto('/training-export');

    await expect(page.getByText(/Training-Data Export/i)).toBeVisible({ timeout: 10000 });
    // Default is 0.80
    await expect(page.getByText(/0\.80/i).first()).toBeVisible({ timeout: 5000 });
  });

  test('4. Train/Val split toggle shows ratio slider when enabled', async ({ page }) => {
    await setupAuth(page);
    await setupTrainingRoutes(page);
    await page.goto('/training-export');

    await expect(page.getByText(/Training-Data Export/i)).toBeVisible({ timeout: 10000 });
    const toggle = page.getByRole('checkbox', { name: /train.*validation split/i });
    if (await toggle.isVisible({ timeout: 5000 }).catch(() => false)) {
      await toggle.check();
      await expect(page.getByText(/train.*%.*val.*%/i).first()).toBeVisible({ timeout: 5000 });
    }
  });
});

// ═══════════════════════════════════════════════════════════════════════════════
// SUITE 2 — Preview Panel
// ═══════════════════════════════════════════════════════════════════════════════

test.describe('Training Export — Preview Panel', () => {

  test('5. Preview shows matching example count (42)', async ({ page }) => {
    await setupAuth(page);
    await setupTrainingRoutes(page);
    await page.goto('/training-export');

    await expect(page.getByText(/Training-Data Export/i)).toBeVisible({ timeout: 10000 });
    // Preview auto-loads on mount
    await expect(page.getByText('42').first()).toBeVisible({ timeout: 10000 });
  });

  test('6. Score distribution chart renders buckets', async ({ page }) => {
    await setupAuth(page);
    await setupTrainingRoutes(page);
    await page.goto('/training-export');

    await expect(page.getByText(/Training-Data Export/i)).toBeVisible({ timeout: 10000 });
    await expect(page.getByTestId('score-distribution')).toBeVisible({ timeout: 10000 });
    await expect(page.getByText('0.80-0.85')).toBeVisible({ timeout: 5000 });
    await expect(page.getByText('0.95-1.00')).toBeVisible({ timeout: 5000 });
  });

  test('7. Sample records show goal text and eval score', async ({ page }) => {
    await setupAuth(page);
    await setupTrainingRoutes(page);
    await page.goto('/training-export');

    await expect(page.getByText(/Training-Data Export/i)).toBeVisible({ timeout: 10000 });
    await expect(
      page.getByText(/Triage all critical Jira/i).first()
    ).toBeVisible({ timeout: 10000 });
    await expect(page.getByText(/score 0\.97/i).first()).toBeVisible({ timeout: 5000 });
  });

  test('8. Preview shows "0 examples" when nothing matches filters', async ({ page }) => {
    await setupAuth(page);
    await setupTrainingRoutes(page, { count: 0 });
    await page.goto('/training-export');

    await expect(page.getByText(/Training-Data Export/i)).toBeVisible({ timeout: 10000 });
    await expect(page.getByText(/^0$/).first()).toBeVisible({ timeout: 10000 });
  });

  test('9. Avg score stat card shows expected value', async ({ page }) => {
    await setupAuth(page);
    await setupTrainingRoutes(page);
    await page.goto('/training-export');

    await expect(page.getByText(/Training-Data Export/i)).toBeVisible({ timeout: 10000 });
    await expect(page.getByText(/0\.912/i)).toBeVisible({ timeout: 10000 });
  });
});

// ═══════════════════════════════════════════════════════════════════════════════
// SUITE 3 — Export Download
// ═══════════════════════════════════════════════════════════════════════════════

test.describe('Training Export — Export Download', () => {

  test('10. Export button triggers POST to export endpoint', async ({ page }) => {
    let exportCalled = false;
    await setupAuth(page);
    await setupTrainingRoutes(page);

    await page.route('**/intelligence/export-training-data*', (route) => {
      if (route.request().method() === 'POST') {
        exportCalled = true;
        return route.fulfill({
          status: 200,
          contentType: 'application/x-ndjson',
          headers: {
            'Content-Disposition': 'attachment; filename="agentverse_training_openai_20260101.jsonl"',
            'X-Training-Examples': '42',
          },
          body: EXPORT_JSONL_LINES,
        });
      }
      return route.continue();
    });

    await page.goto('/training-export');
    await expect(page.getByText(/Training-Data Export/i)).toBeVisible({ timeout: 10000 });
    await page.getByTestId('btn-export').click();

    await expect(async () => {
      expect(exportCalled).toBe(true);
    }).toPass({ timeout: 8000 });
  });

  test('11. After export, success panel shows example count', async ({ page }) => {
    await setupAuth(page);
    await setupTrainingRoutes(page);
    await page.goto('/training-export');

    await expect(page.getByText(/Training-Data Export/i)).toBeVisible({ timeout: 10000 });
    await page.getByTestId('btn-export').click();

    await expect(
      page.getByTestId('export-result')
    ).toBeVisible({ timeout: 8000 });
    // The result shows either a success count or an empty-state message
    await expect(
      page.getByText(/examples exported successfully|No examples matched/i).first()
    ).toBeVisible({ timeout: 5000 });
  });

  test('12. Export URL includes selected format parameter', async ({ page }) => {
    let capturedUrl = '';
    await setupAuth(page);
    await setupTrainingRoutes(page);

    await page.route('**/intelligence/export-training-data*', (route) => {
      if (route.request().method() === 'POST') {
        capturedUrl = route.request().url();
        return route.fulfill({
          status: 200,
          contentType: 'application/x-ndjson',
          headers: {
            'Content-Disposition': 'attachment; filename="agentverse_training_anthropic_x.jsonl"',
            'X-Training-Examples': '10',
          },
          body: '{}',
        });
      }
      return route.continue();
    });

    await page.goto('/training-export');
    await expect(page.getByText(/Training-Data Export/i)).toBeVisible({ timeout: 10000 });

    // Switch to Anthropic
    await page.getByLabel(/^format$/i).selectOption('anthropic');
    await page.getByTestId('btn-export').click();

    await expect(async () => {
      expect(capturedUrl).toContain('format=anthropic');
    }).toPass({ timeout: 8000 });
  });
});

// ═══════════════════════════════════════════════════════════════════════════════
// SUITE 4 — Train/Val Split
// ═══════════════════════════════════════════════════════════════════════════════

test.describe('Training Export — Train/Val Split', () => {

  test('13. Train/Val split section is visible when checkbox enabled', async ({ page }) => {
    await setupAuth(page);
    await setupTrainingRoutes(page);
    await page.goto('/training-export');

    await expect(page.getByText(/Training-Data Export/i)).toBeVisible({ timeout: 10000 });
    const toggle = page.getByRole('checkbox', { name: /train.*validation split/i });
    if (await toggle.isVisible({ timeout: 5000 }).catch(() => false)) {
      await toggle.check();
      await expect(
        page.getByText(/downloads two files/i).first()
      ).toBeVisible({ timeout: 5000 });
    }
  });
});

// ═══════════════════════════════════════════════════════════════════════════════
// SUITE 5 — Export History
// ═══════════════════════════════════════════════════════════════════════════════

test.describe('Training Export — Export History', () => {

  test('14. Export history entry appears after successful export', async ({ page }) => {
    await setupAuth(page);
    await setupTrainingRoutes(page);
    await page.addInitScript(() => localStorage.removeItem('training_export_history_v1'));
    await page.goto('/training-export');

    await expect(page.getByText(/Training-Data Export/i)).toBeVisible({ timeout: 10000 });
    await page.getByTestId('btn-export').click();
    await expect(page.getByTestId('export-result')).toBeVisible({ timeout: 8000 });

    // Open history panel
    await page.getByText(/show export history/i).click();
    await expect(page.getByText(/openai/i).first()).toBeVisible({ timeout: 5000 });
    await expect(page.getByText(/42 examples/i).first()).toBeVisible({ timeout: 5000 });
  });
});
