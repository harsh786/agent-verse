/**
 * Graph Explorer — E2E Tests
 *
 * Covers /knowledge-graph → GraphExplorerPage.tsx (distinct from the /knowledge
 * feature covered by knowledge.spec.ts and knowledge-rag.spec.ts).
 *
 *   1. Page load — header, stats, node list
 *   2. Empty state — no nodes yet
 *   3. Populated state — node list rendered with type/confidence
 *   4. Primary interaction — selecting a node shows its detail + connections
 *   5. Extract-from-text flow
 */
import { test, expect, type Page } from '@playwright/test';
import { setupAuth } from './helpers/auth';

const STATS = { total_nodes: 2, total_edges: 1 };

const NODE_1 = {
  node_id: 'n-1',
  label: 'Customer Onboarding',
  node_type: 'concept',
  confidence: 0.92,
  content: 'Describes the process of onboarding new customers into the platform.',
  metadata: { source: 'goal-123' },
};

const NODE_2 = {
  node_id: 'n-2',
  label: 'Acme Corp',
  node_type: 'entity',
  confidence: 0.81,
  content: 'A customer organization referenced across several goals.',
  metadata: {},
};

const NODE_1_DETAIL = {
  ...NODE_1,
  edges: [
    { edge_id: 'e-1', edge_type: 'relates_to', source_node_id: 'n-1', target_node_id: 'n-2', confidence: 0.75 },
  ],
};

interface RouteOpts {
  nodes?: Record<string, unknown>[];
  stats?: Record<string, unknown>;
}

async function setupGraphRoutes(page: Page, opts: RouteOpts = {}): Promise<void> {
  const nodes = opts.nodes ?? [NODE_1, NODE_2];
  const stats = opts.stats ?? STATS;

  await page.route(/localhost:8000\/knowledge-graph\/nodes(\?.*)?$/, (route) =>
    route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify({ nodes }) })
  );

  await page.route('**/knowledge-graph/nodes/n-1', (route) =>
    route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(NODE_1_DETAIL) })
  );

  await page.route('**/knowledge-graph/nodes/n-2', (route) =>
    route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify({ ...NODE_2, edges: [] }) })
  );

  await page.route('**/knowledge-graph/stats', (route) =>
    route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(stats) })
  );

  await page.route('**/knowledge-graph/extract', async (route) => {
    return route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({ entities_extracted: 3, relationships_extracted: 2 }),
    });
  });
}

// ═══════════════════════════════════════════════════════════════════════════════
// SUITE 1 — Page load
// ═══════════════════════════════════════════════════════════════════════════════

test.describe('Graph Explorer — page load', () => {
  test('1. Renders header, node/edge stats, and the node list', async ({ page }) => {
    await setupAuth(page);
    await setupGraphRoutes(page);
    await page.goto('/knowledge-graph');

    await expect(page.getByRole('heading', { name: 'Graph Explorer' })).toBeVisible({ timeout: 10000 });
    await expect(page.getByText('2', { exact: true }).first()).toBeVisible();
    await expect(page.getByText('Nodes')).toBeVisible();
    await expect(page.getByText('Edges')).toBeVisible();

    await expect(page.getByText('Customer Onboarding')).toBeVisible();
    await expect(page.getByText('Acme Corp')).toBeVisible();
  });

  test('2. Shows the list/graph view toggle and search input', async ({ page }) => {
    await setupAuth(page);
    await setupGraphRoutes(page);
    await page.goto('/knowledge-graph');

    await expect(page.getByRole('heading', { name: 'Graph Explorer' })).toBeVisible({ timeout: 10000 });
    await expect(page.getByRole('button', { name: 'list', exact: true })).toBeVisible();
    await expect(page.getByRole('button', { name: 'graph', exact: true })).toBeVisible();
    await expect(page.getByPlaceholder('Search nodes...')).toBeVisible();
  });
});

// ═══════════════════════════════════════════════════════════════════════════════
// SUITE 2 — Empty state
// ═══════════════════════════════════════════════════════════════════════════════

test.describe('Graph Explorer — empty state', () => {
  test('3. Shows "No nodes yet" when the graph is empty', async ({ page }) => {
    await setupAuth(page);
    await setupGraphRoutes(page, { nodes: [], stats: { total_nodes: 0, total_edges: 0 } });
    await page.goto('/knowledge-graph');

    await expect(page.getByRole('heading', { name: 'Graph Explorer' })).toBeVisible({ timeout: 10000 });
    await expect(page.getByText('No nodes yet')).toBeVisible({ timeout: 5000 });
    await expect(page.getByText('Extract text to populate the graph')).toBeVisible();
  });

  test('4. Right panel shows placeholder prompt when no node is selected', async ({ page }) => {
    await setupAuth(page);
    await setupGraphRoutes(page);
    await page.goto('/knowledge-graph');

    await expect(page.getByRole('heading', { name: 'Graph Explorer' })).toBeVisible({ timeout: 10000 });
    await expect(page.getByText('Select a node to explore')).toBeVisible({ timeout: 5000 });
  });
});

// ═══════════════════════════════════════════════════════════════════════════════
// SUITE 3 — Populated state
// ═══════════════════════════════════════════════════════════════════════════════

test.describe('Graph Explorer — populated state', () => {
  test('5. Node list shows type and confidence for each node', async ({ page }) => {
    await setupAuth(page);
    await setupGraphRoutes(page);
    await page.goto('/knowledge-graph');

    await expect(page.getByText('Customer Onboarding')).toBeVisible({ timeout: 10000 });
    await expect(page.getByText(/concept.*92% confidence/i)).toBeVisible();
    await expect(page.getByText(/entity.*81% confidence/i)).toBeVisible();
  });

  test('6. Node type filter narrows the visible nodes', async ({ page }) => {
    await setupAuth(page);
    // The node-type filter is server-driven (query param), so return only the
    // matching node once the filter chip is applied.
    let currentFilter: string | null = null;
    await page.route(/localhost:8000\/knowledge-graph\/nodes(\?.*)?$/, (route) => {
      const url = new URL(route.request().url());
      currentFilter = url.searchParams.get('node_type');
      const nodes = currentFilter ? [NODE_1, NODE_2].filter((n) => n.node_type === currentFilter) : [NODE_1, NODE_2];
      return route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify({ nodes }) });
    });
    await page.route('**/knowledge-graph/stats', (route) =>
      route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(STATS) })
    );

    await page.goto('/knowledge-graph');
    await expect(page.getByText('Customer Onboarding')).toBeVisible({ timeout: 10000 });
    await expect(page.getByText('Acme Corp')).toBeVisible();

    await page.getByRole('button', { name: 'entity', exact: true }).click();

    await expect(page.getByText('Acme Corp')).toBeVisible({ timeout: 5000 });
    await expect(page.getByText('Customer Onboarding')).toHaveCount(0);
  });
});

// ═══════════════════════════════════════════════════════════════════════════════
// SUITE 4 — Primary interaction: select a node
// ═══════════════════════════════════════════════════════════════════════════════

test.describe('Graph Explorer — node selection', () => {
  test('7. Clicking a node shows its detail panel with content and connections', async ({ page }) => {
    await setupAuth(page);
    await setupGraphRoutes(page);
    await page.goto('/knowledge-graph');

    await expect(page.getByText('Customer Onboarding')).toBeVisible({ timeout: 10000 });
    await page.getByText('Customer Onboarding').click();

    await expect(
      page.getByRole('heading', { name: 'Customer Onboarding' })
    ).toBeVisible({ timeout: 5000 });
    await expect(page.getByText('Describes the process of onboarding new customers into the platform.')).toBeVisible();
    await expect(page.getByText('Connections (1)')).toBeVisible();
    await expect(page.getByText('relates_to')).toBeVisible();
  });
});

// ═══════════════════════════════════════════════════════════════════════════════
// SUITE 5 — Extract from text
// ═══════════════════════════════════════════════════════════════════════════════

test.describe('Graph Explorer — extract from text', () => {
  test('8. Extract panel opens, submits text, and shows a success toast', async ({ page }) => {
    await setupAuth(page);
    await setupGraphRoutes(page);
    await page.goto('/knowledge-graph');

    await expect(page.getByRole('heading', { name: 'Graph Explorer' })).toBeVisible({ timeout: 10000 });
    await page.getByRole('button', { name: /extract/i }).click();

    await expect(page.getByText('Extract from Text')).toBeVisible({ timeout: 5000 });
    await page.getByPlaceholder('Paste text to extract entities and relationships...').fill(
      'Acme Corp signed a new contract with Globex.'
    );
    await page.getByRole('button', { name: 'Extract', exact: true }).click();

    await expect(
      page.getByText('Extracted 3 entities, 2 relationships')
    ).toBeVisible({ timeout: 5000 });
  });
});
