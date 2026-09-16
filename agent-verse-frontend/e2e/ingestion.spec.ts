/**
 * Knowledge Sources (Ingestion) — E2E Tests
 *
 * Covers /sources → SourcesPage.tsx:
 *   1. Page load — header, stat cards, family filter chips
 *   2. Empty state — no sources yet
 *   3. Populated state — source cards, family filter chip counts/filtering
 *   4. Create-source wizard — family → type → configure → submit (database family)
 */
import { test, expect, type Page } from '@playwright/test';
import { setupAuth } from './helpers/auth';

// ── Mock data ─────────────────────────────────────────────────────────────────

const QUOTA = {
  tenant_id: 'test-tenant',
  plan: 'professional',
  sources_used: 2,
  sources_limit: 25,
  docs_used: 1200,
  docs_limit: 100000,
  tokens_used_month: 500000,
  tokens_limit_month: 5000000,
  cost_usd_month: 4.2,
};

const SOURCE_S3 = {
  source_id: 'src-s3-1',
  tenant_id: 'test-tenant',
  name: 'Prod Data Lake',
  family: 'object_storage',
  source_type: 's3',
  enabled: true,
  sync_mode: 'incremental',
  last_synced_at: '2026-09-10T12:00:00Z',
  total_docs_indexed: 4200,
  total_chunks: 18900,
};

const SOURCE_SLACK = {
  source_id: 'src-slack-1',
  tenant_id: 'test-tenant',
  name: 'Team Slack',
  family: 'communication',
  source_type: 'slack',
  enabled: false,
  sync_mode: 'streaming',
  last_synced_at: null,
  total_docs_indexed: 300,
  total_chunks: 900,
};

const HEALTH_OK = { ok: true, latency_ms: 42, error: null, metadata: {} };

// ── Route setup ───────────────────────────────────────────────────────────────

interface RouteOpts {
  sources?: Record<string, unknown>[];
  quota?: Record<string, unknown> | null;
  onCreate?: (payload: Record<string, unknown>) => void;
}

async function setupIngestionRoutes(page: Page, opts: RouteOpts = {}): Promise<void> {
  const sources = opts.sources ?? [SOURCE_S3, SOURCE_SLACK];
  const quota = opts.quota === undefined ? QUOTA : opts.quota;

  await page.route(/localhost:8000\/sources/, async (route) => {
    const method = route.request().method();
    const url = route.request().url();

    // GET /sources/{id}/health
    if (method === 'GET' && /\/sources\/[^/?]+\/health/.test(url)) {
      return route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(HEALTH_OK) });
    }

    // POST /sources (create)
    if (method === 'POST') {
      const payload = JSON.parse(route.request().postData() ?? '{}');
      opts.onCreate?.(payload);
      return route.fulfill({
        status: 201,
        contentType: 'application/json',
        body: JSON.stringify({
          source_id: 'src-new-1',
          tenant_id: 'test-tenant',
          enabled: true,
          last_synced_at: null,
          total_docs_indexed: 0,
          total_chunks: 0,
          ...payload,
        }),
      });
    }

    // GET /sources (list)
    if (method === 'GET' && /\/sources\/?(\?.*)?$/.test(url)) {
      return route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(sources) });
    }

    return route.continue();
  });

  await page.route('**/ingestion/quota', (route) => {
    if (quota === null) {
      return route.fulfill({ status: 404, contentType: 'application/json', body: JSON.stringify({ detail: 'not found' }) });
    }
    return route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(quota) });
  });
}

// ═══════════════════════════════════════════════════════════════════════════════
// SUITE 1 — Page load
// ═══════════════════════════════════════════════════════════════════════════════

test.describe('Sources — page load', () => {
  test('1. Renders header, stat cards, and family filter chips', async ({ page }) => {
    await setupAuth(page);
    await setupIngestionRoutes(page);
    await page.goto('/sources');

    await expect(page.getByRole('heading', { name: 'Knowledge Sources' })).toBeVisible({ timeout: 10000 });
    await expect(page.getByText('Total Sources')).toBeVisible();
    await expect(page.getByText('Active')).toBeVisible();
    await expect(page.getByText('Families')).toBeVisible();

    // Family filter chips (checkbox role)
    await expect(page.getByRole('checkbox', { name: 'All' })).toBeVisible();
    await expect(page.getByRole('checkbox', { name: 'Object Storage', exact: false })).toBeVisible();
  });

  test('2. Shows quota usage bar when quota data is available', async ({ page }) => {
    await setupAuth(page);
    await setupIngestionRoutes(page);
    await page.goto('/sources');

    await expect(page.getByRole('heading', { name: 'Knowledge Sources' })).toBeVisible({ timeout: 10000 });
    // QuotaUsageBar renders somewhere referencing sources used/limit — assert plan text shows up.
    await expect(page.getByText(/professional/i).first()).toBeVisible({ timeout: 5000 });
  });
});

// ═══════════════════════════════════════════════════════════════════════════════
// SUITE 2 — Empty state
// ═══════════════════════════════════════════════════════════════════════════════

test.describe('Sources — empty state', () => {
  test('3. Shows empty state with call-to-action when there are no sources', async ({ page }) => {
    await setupAuth(page);
    await setupIngestionRoutes(page, { sources: [] });
    await page.goto('/sources');

    await expect(page.getByRole('heading', { name: 'Knowledge Sources' })).toBeVisible({ timeout: 10000 });
    await expect(page.getByText('No knowledge sources yet')).toBeVisible({ timeout: 5000 });
    await expect(page.getByRole('button', { name: 'Add your first source' })).toBeVisible();
  });

  test('4. Clicking "Add your first source" opens the create wizard', async ({ page }) => {
    await setupAuth(page);
    await setupIngestionRoutes(page, { sources: [] });
    await page.goto('/sources');

    await expect(page.getByText('No knowledge sources yet')).toBeVisible({ timeout: 10000 });
    await page.getByRole('button', { name: 'Add your first source' }).click();

    await expect(page.getByRole('dialog', { name: 'Add knowledge source' })).toBeVisible({ timeout: 5000 });
    await expect(page.getByText('Choose a source family')).toBeVisible();
  });
});

// ═══════════════════════════════════════════════════════════════════════════════
// SUITE 3 — Populated state
// ═══════════════════════════════════════════════════════════════════════════════

test.describe('Sources — populated state', () => {
  test('5. Renders source cards with name, type, family, and doc/chunk counts', async ({ page }) => {
    await setupAuth(page);
    await setupIngestionRoutes(page);
    await page.goto('/sources');

    await expect(page.getByRole('heading', { name: 'Knowledge Sources' })).toBeVisible({ timeout: 10000 });

    const s3Card = page.getByRole('article', { name: /Prod Data Lake/i });
    await expect(s3Card).toBeVisible({ timeout: 5000 });
    await expect(s3Card.getByText('s3')).toBeVisible();
    await expect(s3Card.getByText('4,200 docs')).toBeVisible();

    const slackCard = page.getByRole('article', { name: /Team Slack/i });
    await expect(slackCard).toBeVisible();
    await expect(slackCard.getByText('slack')).toBeVisible();
  });

  test('6. Family filter chip narrows the source list', async ({ page }) => {
    await setupAuth(page);
    await setupIngestionRoutes(page);
    await page.goto('/sources');

    await expect(page.getByRole('article', { name: /Prod Data Lake/i })).toBeVisible({ timeout: 10000 });
    await expect(page.getByRole('article', { name: /Team Slack/i })).toBeVisible();

    // Filter to Communication family only.
    await page.getByRole('checkbox', { name: /Communication/i }).click();

    await expect(page.getByRole('article', { name: /Team Slack/i })).toBeVisible();
    await expect(page.getByRole('article', { name: /Prod Data Lake/i })).toHaveCount(0);
  });

  test('7. Search filters the source list by name', async ({ page }) => {
    await setupAuth(page);
    await setupIngestionRoutes(page);
    await page.goto('/sources');

    await expect(page.getByRole('article', { name: /Prod Data Lake/i })).toBeVisible({ timeout: 10000 });
    await page.getByLabel('Search sources').fill('Slack');

    await expect(page.getByRole('article', { name: /Team Slack/i })).toBeVisible();
    await expect(page.getByRole('article', { name: /Prod Data Lake/i })).toHaveCount(0);
  });

  test('8. Shows an error banner with retry when sources fail to load', async ({ page }) => {
    await setupAuth(page);
    await page.route(/localhost:8000\/sources\/?(\?.*)?$/, (route) => {
      if (route.request().method() === 'GET') {
        return route.fulfill({ status: 500, contentType: 'application/json', body: JSON.stringify({ detail: 'boom' }) });
      }
      return route.continue();
    });
    await page.route('**/ingestion/quota', (route) =>
      route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(QUOTA) })
    );
    await page.goto('/sources');

    await expect(page.getByRole('alert')).toBeVisible({ timeout: 10000 });
    await expect(page.getByText('Failed to load sources.')).toBeVisible();
    await expect(page.getByRole('button', { name: /retry/i })).toBeVisible();
  });
});

// ═══════════════════════════════════════════════════════════════════════════════
// SUITE 4 — Create-source wizard (end to end, database family)
// ═══════════════════════════════════════════════════════════════════════════════

test.describe('Sources — create wizard', () => {
  test('9. Full flow: pick Relational DB / postgresql, configure, and submit', async ({ page }) => {
    let created: Record<string, unknown> | null = null;
    await setupAuth(page);
    await setupIngestionRoutes(page, { onCreate: (payload) => { created = payload; } });
    await page.goto('/sources');

    await expect(page.getByRole('heading', { name: 'Knowledge Sources' })).toBeVisible({ timeout: 10000 });
    await page.getByRole('button', { name: 'Add Source' }).click();

    const dialog = page.getByRole('dialog', { name: 'Add knowledge source' });
    await expect(dialog).toBeVisible({ timeout: 5000 });

    // Step 1: family
    await expect(dialog.getByText('Choose a source family')).toBeVisible();
    await dialog.getByText('Relational DB', { exact: true }).click();

    // Step 2: type
    await expect(dialog.getByText('postgresql', { exact: true })).toBeVisible({ timeout: 5000 });
    await dialog.getByText('postgresql', { exact: true }).click();

    // Step 3: configure
    await expect(dialog.getByText('Source Name *')).toBeVisible({ timeout: 5000 });
    await expect(dialog.getByText('Host')).toBeVisible();
    await expect(dialog.getByText('CDC Mode')).toBeVisible();

    await dialog.getByPlaceholder('My postgresql source').fill('Analytics DB');
    await dialog.getByPlaceholder('db.example.com').fill('db.internal.acme.com');
    await dialog.locator('input[type="number"]').fill('5432');

    const submitBtn = dialog.getByRole('button', { name: /create source/i });
    await expect(submitBtn).toBeDisabled();
    // Filling the name field above should have enabled it already, but re-assert post-fill.
    await expect(submitBtn).not.toBeDisabled({ timeout: 3000 });
    await submitBtn.click();

    // Wizard closes on success.
    await expect(dialog).toHaveCount(0, { timeout: 5000 });

    await expect(async () => {
      expect(created).toMatchObject({
        name: 'Analytics DB',
        family: 'oltp_database',
        source_type: 'postgresql',
      });
    }).toPass({ timeout: 5000 });
  });

  test('10. Cancel button closes the wizard without creating a source', async ({ page }) => {
    let createCalled = false;
    await setupAuth(page);
    await setupIngestionRoutes(page, { onCreate: () => { createCalled = true; } });
    await page.goto('/sources');

    await expect(page.getByRole('heading', { name: 'Knowledge Sources' })).toBeVisible({ timeout: 10000 });
    await page.getByRole('button', { name: 'Add Source' }).click();

    const dialog = page.getByRole('dialog', { name: 'Add knowledge source' });
    await dialog.getByText('Relational DB', { exact: true }).click();
    await expect(dialog.getByText('postgresql', { exact: true })).toBeVisible({ timeout: 5000 });
    await dialog.getByText('postgresql', { exact: true }).click();
    await expect(dialog.getByText('Source Name *')).toBeVisible({ timeout: 5000 });

    await dialog.getByRole('button', { name: /^cancel$/i }).click();
    await expect(dialog).toHaveCount(0);
    expect(createCalled).toBe(false);
  });
});
