/**
 * Graphify — E2E Tests
 *
 * Covers /graphify (GraphifyPage), which wraps GraphifyProgress and
 * InteractiveKnowledgeGraph:
 *   1. Renders heading and org selector, auto-selecting the first org
 *   2. Empty state — no organisations found
 *   3. Populated + primary interaction — start build, stream phases via SSE,
 *      reach "complete" and reveal the knowledge graph viewer
 */
import { test, expect, type Page } from '@playwright/test';
import { setupAuth } from './helpers/auth';

async function mockOrgs(page: Page, orgs: { id: string; name: string }[]): Promise<void> {
  await page.route('**/v1/org', (route) => {
    if (route.request().method() !== 'GET') return route.continue();
    return route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(orgs) });
  });
}

async function mockGraphifyRun(
  page: Page,
  orgId: string,
  opts: { jobId?: string; sseBody?: string } = {}
): Promise<void> {
  const jobId = opts.jobId ?? 'job-1';

  await page.route(`**/api/v1/org/${orgId}/graphify`, (route) => {
    if (route.request().method() !== 'POST') return route.continue();
    return route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({ job_id: jobId }),
    });
  });

  await page.route('**/tenants/stream-token', (route) =>
    route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify({ token: 'stream-tok' }) })
  );

  const defaultSse =
    'data: {"type":"connected"}\n\n' +
    'data: {"type":"phase","phase":1,"total_phases":4,"label":"Scanning entities"}\n\n' +
    'data: {"type":"complete","nodes":42,"edges":88,"communities":5,"discoveries":3}\n\n';

  await page.route(`**/api/v1/org/${orgId}/graphify/${jobId}/stream**`, (route) =>
    route.fulfill({ status: 200, contentType: 'text/event-stream', body: opts.sseBody ?? defaultSse })
  );
}

async function mockKnowledgeGraphExport(page: Page): Promise<void> {
  await page.route('**/knowledge-graph/export', (route) =>
    route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({
        tenant_id: 'test-tenant',
        exported_at: new Date().toISOString(),
        nodes: [],
        edges: [],
        stats: { nodes: 0, edges: 0 },
        format: 'json',
      }),
    })
  );
}

// ═══════════════════════════════════════════════════════════════════════════════
// SUITE — /graphify
// ═══════════════════════════════════════════════════════════════════════════════

test.describe('Graphify page', () => {
  test('1. Renders heading and auto-selects the first organisation', async ({ page }) => {
    await setupAuth(page);
    await mockOrgs(page, [
      { id: 'org-1', name: 'Acme Corp' },
      { id: 'org-2', name: 'Globex Inc' },
    ]);

    await page.goto('/graphify');

    await expect(page.getByRole('heading', { name: 'Graphify' })).toBeVisible({ timeout: 10000 });
    await expect(page.getByText('Select Organisation')).toBeVisible();
    await expect(page.getByText('Acme Corp')).toBeVisible({ timeout: 5000 });
    await expect(page.getByText('Globex Inc')).toBeVisible();

    // First org auto-selected → highlighted button + Start Graphify visible.
    await expect(page.getByRole('button', { name: /start graphify/i })).toBeVisible({ timeout: 5000 });
  });

  test('2. Empty state — shows "No organisations found" message', async ({ page }) => {
    await setupAuth(page);
    await mockOrgs(page, []);

    await page.goto('/graphify');

    await expect(page.getByRole('heading', { name: 'Graphify' })).toBeVisible({ timeout: 10000 });
    await expect(page.getByText(/no organisations found/i)).toBeVisible({ timeout: 5000 });
    await expect(page.getByRole('button', { name: /start graphify/i })).not.toBeVisible();
  });

  test('3. Populated — starting a build streams progress to completion and reveals the graph', async ({ page }) => {
    await setupAuth(page);
    await mockOrgs(page, [{ id: 'org-1', name: 'Acme Corp' }]);
    await mockGraphifyRun(page, 'org-1');
    await mockKnowledgeGraphExport(page);

    await page.goto('/graphify');
    await expect(page.getByRole('heading', { name: 'Graphify' })).toBeVisible({ timeout: 10000 });
    await expect(page.getByRole('button', { name: /start graphify/i })).toBeVisible({ timeout: 5000 });

    await page.getByRole('button', { name: /start graphify/i }).click();

    // Progress panel appears and streams through to "Complete".
    await expect(page.getByRole('status', { name: /graphify:/i })).toBeVisible({ timeout: 5000 });
    await expect(page.getByText(/graph built.*42 nodes.*88 edges/i)).toBeVisible({ timeout: 10000 });

    // Completion reveals the "done" panel with the knowledge graph viewer.
    await expect(page.getByText(/knowledge graph built.*explore it below/i)).toBeVisible({ timeout: 5000 });
    await expect(page.getByRole('link', { name: /open full explorer/i })).toBeVisible();
  });
});
