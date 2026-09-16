/**
 * Eval Suites E2E Tests
 *
 * Covers /eval-suites (src/features/eval-suites/EvalSuitesPage.tsx) — a distinct page
 * from the /eval feature (see eval.spec.ts / eval.eval.spec.ts / eval-scorecard.spec.ts,
 * which are NOT touched here).
 *
 *   1. Loads and renders header + KPI row
 *   2. Empty state when no suites exist
 *   3. Populated state renders suite cards with status/pass-rate
 *   4. Primary interaction: create a new suite via the modal
 *   5. Primary interaction: run a suite
 */
import { test, expect, type Page } from '@playwright/test';
import { setupAuth } from './helpers/auth';

// ── Mock data ─────────────────────────────────────────────────────────────────

interface MockSuite {
  id: string;
  name: string;
  description?: string;
  task_count: number;
  last_run_at?: string;
  last_run_status?: 'passed' | 'failed' | 'running' | 'pending';
  pass_rate?: number;
  created_at: string;
}

async function mockEvalSuitesApi(
  page: Page,
  { suites = [] as MockSuite[], created }: { suites?: MockSuite[]; created?: MockSuite } = {}
): Promise<void> {
  await page.route(/localhost:8000\/intelligence\/eval-suites/, async (route) => {
    const method = route.request().method();
    const url = route.request().url();

    // POST /intelligence/eval-suites/{id}/run
    if (method === 'POST' && url.match(/\/run$/)) {
      return route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify({ status: 'running' }) });
    }

    // POST /intelligence/eval-suites (create)
    if (method === 'POST') {
      const body = route.request().postDataJSON() as { name: string; description: string };
      const stub: MockSuite = created ?? {
        id: 'suite-new',
        name: body.name,
        description: body.description,
        task_count: 0,
        created_at: new Date().toISOString(),
      };
      return route.fulfill({ status: 201, contentType: 'application/json', body: JSON.stringify(stub) });
    }

    // GET (list)
    return route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(suites) });
  });
}

const SAMPLE_SUITES: MockSuite[] = [
  {
    id: 'suite-1',
    name: 'Q1 Quality Benchmarks',
    description: 'Core regression suite for support agents',
    task_count: 12,
    last_run_at: '2026-01-05T00:00:00Z',
    last_run_status: 'passed',
    pass_rate: 0.92,
    created_at: '2026-01-01T00:00:00Z',
  },
  {
    id: 'suite-2',
    name: 'Jira Triage Suite',
    task_count: 5,
    last_run_at: '2026-01-06T00:00:00Z',
    last_run_status: 'failed',
    pass_rate: 0.4,
    created_at: '2026-01-02T00:00:00Z',
  },
];

// ═══════════════════════════════════════════════════════════════════════════════

test.describe('Eval Suites — Page load', () => {
  test('1. Renders header, subtitle, and KPI row', async ({ page }) => {
    await setupAuth(page);
    await mockEvalSuitesApi(page, { suites: SAMPLE_SUITES });
    await page.goto('/eval-suites');

    await expect(page.getByRole('heading', { name: /eval suites/i })).toBeVisible({ timeout: 10000 });
    await expect(page.getByText(/run automated evaluation suites/i)).toBeVisible();
    await expect(page.getByText('Total Suites')).toBeVisible();
    await expect(page.getByText('Passed')).toBeVisible();
    await expect(page.getByText('Failed')).toBeVisible();
  });

  test('2. Shows error state when the API call fails', async ({ page }) => {
    await setupAuth(page);
    await page.route(/localhost:8000\/intelligence\/eval-suites/, (route) =>
      route.fulfill({ status: 500, contentType: 'application/json', body: JSON.stringify({ detail: 'boom' }) })
    );
    await page.goto('/eval-suites');

    await expect(page.getByRole('alert')).toContainText(/failed to load eval suites/i, { timeout: 10000 });
  });
});

test.describe('Eval Suites — Empty state', () => {
  test('3. Shows empty state when no suites exist', async ({ page }) => {
    await setupAuth(page);
    await mockEvalSuitesApi(page, { suites: [] });
    await page.goto('/eval-suites');

    await expect(page.getByRole('heading', { name: /eval suites/i })).toBeVisible({ timeout: 10000 });
    await expect(page.getByText(/no eval suites yet/i)).toBeVisible();
  });
});

test.describe('Eval Suites — Populated state', () => {
  test('4. Renders suite cards with task counts, pass rate, and status badges', async ({ page }) => {
    await setupAuth(page);
    await mockEvalSuitesApi(page, { suites: SAMPLE_SUITES });
    await page.goto('/eval-suites');

    await expect(page.getByText('Q1 Quality Benchmarks')).toBeVisible({ timeout: 10000 });
    await expect(page.getByText('Jira Triage Suite')).toBeVisible();
    await expect(page.getByText('12 tasks')).toBeVisible();
    await expect(page.getByText('92% pass')).toBeVisible();
    await expect(page.getByText('Passed', { exact: true })).toBeVisible();
    await expect(page.getByText('Failed', { exact: true })).toBeVisible();
  });
});

test.describe('Eval Suites — Create suite interaction', () => {
  test('5. Opens the create-suite modal, fills the form, and submits', async ({ page }) => {
    await setupAuth(page);
    await mockEvalSuitesApi(page, { suites: [] });
    await page.goto('/eval-suites');

    await expect(page.getByText(/no eval suites yet/i)).toBeVisible({ timeout: 10000 });

    await page.getByRole('button', { name: /new suite/i }).click();
    const dialog = page.getByRole('dialog');
    await expect(dialog).toBeVisible();
    await expect(dialog.getByText(/new eval suite/i)).toBeVisible();

    await dialog.getByPlaceholder(/q1 quality benchmarks/i).fill('Support Agent Regression');
    await dialog.getByPlaceholder(/what does this suite evaluate/i).fill('Covers support triage flows');

    await dialog.getByRole('button', { name: /create suite/i }).click();
    await expect(dialog).toBeHidden({ timeout: 5000 });
  });

  test('6. Create button is disabled until a name is entered', async ({ page }) => {
    await setupAuth(page);
    await mockEvalSuitesApi(page, { suites: [] });
    await page.goto('/eval-suites');

    await expect(page.getByText(/no eval suites yet/i)).toBeVisible({ timeout: 10000 });
    await page.getByRole('button', { name: /new suite/i }).click();

    const dialog = page.getByRole('dialog');
    const createBtn = dialog.getByRole('button', { name: /create suite/i });
    await expect(createBtn).toBeDisabled();

    await dialog.getByPlaceholder(/q1 quality benchmarks/i).fill('Support Agent Regression');
    await expect(createBtn).toBeEnabled();
  });
});

test.describe('Eval Suites — Run suite interaction', () => {
  test('7. Clicking Run on a suite card triggers the run endpoint', async ({ page }) => {
    let ranSuiteId = '';
    await setupAuth(page);
    await mockEvalSuitesApi(page, { suites: SAMPLE_SUITES });
    await page.route(/localhost:8000\/intelligence\/eval-suites\/[^/]+\/run/, (route) => {
      ranSuiteId = route.request().url().split('/eval-suites/')[1].split('/run')[0];
      return route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify({ status: 'running' }) });
    });
    await page.goto('/eval-suites');

    await expect(page.getByText('Q1 Quality Benchmarks')).toBeVisible({ timeout: 10000 });
    await page.getByRole('button', { name: /^run$/i }).first().click();

    await expect(async () => {
      expect(ranSuiteId).toBe('suite-1');
    }).toPass({ timeout: 5000 });
  });
});
