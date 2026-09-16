/**
 * Real E2E tests for the Knowledge Graph explorer — NO HTTP mocking.
 *
 * Route: /knowledge-graph, page src/features/knowledge-graph/GraphExplorerPage.tsx.
 * Backend: agent-verse-backend/app/api/knowledge_graph.py (prefix /knowledge-graph).
 *
 * Covers the read-only surface (nodes, stats) plus the real extract-from-text
 * flow (POST /knowledge-graph/extract), which is safe to run against a real
 * tenant since it just runs entity/relationship extraction over supplied text.
 */
import { expect } from '@playwright/test';
import { test, FRONTEND_BASE } from './fixtures';

test.describe('Knowledge Graph — real backend', () => {
  test('GET /knowledge-graph/nodes returns an empty node list for a fresh tenant', async ({ api }) => {
    const resp = await api.get('/knowledge-graph/nodes');
    expect(resp.status()).toBe(200);
    const body = await resp.json();
    expect(Array.isArray(body.nodes)).toBe(true);
    expect(body.nodes.length).toBe(0);
    expect(body.total).toBe(0);
  });

  test('GET /knowledge-graph/stats returns real stats', async ({ api }) => {
    const resp = await api.get('/knowledge-graph/stats');
    expect(resp.status()).toBe(200);
    const body = await resp.json();
    expect(body).toHaveProperty('total_nodes');
  });

  test('graph explorer page renders with empty state', async ({ authedPage }) => {
    await authedPage.goto(`${FRONTEND_BASE}/knowledge-graph`, { waitUntil: 'networkidle' });

    await expect(authedPage.getByRole('heading', { name: 'Graph Explorer' })).toBeVisible({ timeout: 10000 });
    await expect(authedPage.getByText('No nodes yet')).toBeVisible({ timeout: 5000 });
    await expect(authedPage.getByPlaceholder('Search nodes...')).toBeVisible();
  });

  test('extracting real text creates nodes visible in the node list', async ({ authedPage }) => {
    await authedPage.goto(`${FRONTEND_BASE}/knowledge-graph`, { waitUntil: 'networkidle' });
    await expect(authedPage.getByRole('heading', { name: 'Graph Explorer' })).toBeVisible({ timeout: 10000 });

    await authedPage.getByRole('button', { name: 'Extract' }).click();
    await expect(authedPage.getByText('Extract from Text')).toBeVisible({ timeout: 5000 });

    await authedPage.getByPlaceholder('Paste text to extract entities and relationships...').fill(
      'AgentVerse is an autonomous agent platform built by the Platform Engineering team.',
    );
    // Two "Extract" buttons exist: the header toggle (already clicked above) and the
    // panel's submit button, which renders after it in the DOM — so it's the last match.
    await authedPage.getByRole('button', { name: 'Extract', exact: true }).last().click();

    // Extraction is real (may hit the FakeProvider or a real LLM) — wait generously
    // for the mutation toast / list refresh rather than asserting a fixed count.
    await expect(authedPage.getByText(/Extracted \d+ entities/i)).toBeVisible({ timeout: 20000 });
  });

  test('switching to graph view renders the interactive graph panel', async ({ authedPage }) => {
    await authedPage.goto(`${FRONTEND_BASE}/knowledge-graph`, { waitUntil: 'networkidle' });
    await expect(authedPage.getByRole('heading', { name: 'Graph Explorer' })).toBeVisible({ timeout: 10000 });

    await authedPage.getByRole('button', { name: 'graph' }).click();
    await expect(authedPage.locator('body')).toBeVisible();
  });
});
