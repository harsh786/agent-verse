import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { render, screen, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, test, vi } from 'vitest';
import { useAuthStore } from '@/stores/auth';
import { ObsidianVaultExplorer } from './ObsidianVaultExplorer';

function renderExplorer() {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={qc}>
      <ObsidianVaultExplorer orgId="org-1" />
    </QueryClientProvider>
  );
}

const REAL_GRAPH_PAYLOAD = {
  tenant_id: 'tenant-1',
  exported_at: '2026-09-01T00:00:00Z',
  nodes: [
    { node_id: 'n1', node_type: 'document', label: 'Runbook.md', confidence: 0.95, source_id: 'doc-1' },
    { node_id: 'n2', node_type: 'entity', label: 'AgentVerse', confidence: 0.8, source_id: 'doc-1' },
    { node_id: 'n3', node_type: 'concept', label: 'Multi-tenancy', confidence: 0.7, source_id: 'doc-1' },
  ],
  edges: [
    { edge_id: 'e1', edge_type: 'mentions', source: 'n1', target: 'n2', confidence: 0.9 },
    { edge_id: 'e2', edge_type: 'references', source: 'n1', target: 'n3', confidence: 0.6 },
  ],
  stats: { nodes: 3, edges: 2 },
  format: 'agentverse_kg_v1',
};

function mockFetch(payload: unknown) {
  return vi.spyOn(globalThis, 'fetch').mockImplementation(async (input) => {
    const url = String(input);
    if (url.includes('/knowledge-graph/export'))
      return new Response(JSON.stringify(payload), { status: 200, headers: { 'Content-Type': 'application/json' } });
    if (url.includes('/v1/org'))
      return new Response(JSON.stringify([]), { status: 200, headers: { 'Content-Type': 'application/json' } });
    return new Response('{}', { status: 200 });
  });
}

beforeEach(() => {
  sessionStorage.clear();
  localStorage.clear();
  useAuthStore.setState({ apiKey: 'test-key', tenantId: 'tenant-1', plan: 'free', isAuthenticated: true });
});
afterEach(() => vi.restoreAllMocks());

describe('ObsidianVaultExplorer — Graph tab (real backend data)', () => {
  test('renders nodes/edges and type filters from a mocked real /knowledge-graph/export payload', async () => {
    mockFetch(REAL_GRAPH_PAYLOAD);
    renderExplorer();

    // Type filter chips are derived from real node types in the payload
    // (the legend below the graph also lists each type, hence findAllByText).
    expect((await screen.findAllByText('document')).length).toBeGreaterThan(0);
    expect(screen.getAllByText('entity').length).toBeGreaterThan(0);
    expect(screen.getAllByText('concept').length).toBeGreaterThan(0);

    // The force-directed graph canvas renders (real KnowledgeGraph component).
    await waitFor(() => {
      expect(screen.getByLabelText('Knowledge graph view')).toBeInTheDocument();
    });

    // No hardcoded demo labels should ever appear.
    expect(screen.queryByText('Q3 Strategy')).not.toBeInTheDocument();
    expect(screen.queryByText('SEBI Compliance')).not.toBeInTheDocument();
  });

  test('shows an honest empty state when the tenant knowledge graph has no nodes', async () => {
    mockFetch({ tenant_id: 'tenant-1', exported_at: '2026-09-01T00:00:00Z', nodes: [], edges: [], stats: { nodes: 0, edges: 0 }, format: 'agentverse_kg_v1' });
    renderExplorer();

    expect(await screen.findByText(/knowledge graph is empty/i)).toBeInTheDocument();
    // No demo/fabricated nodes should be rendered as a fallback.
    expect(screen.queryByText('Q3 Strategy')).not.toBeInTheDocument();
    expect(screen.queryByLabelText('Knowledge graph visualization')).not.toBeInTheDocument();
  });

  test('shows a retry option on a failed graph fetch (no fabricated fallback data)', async () => {
    vi.spyOn(globalThis, 'fetch').mockImplementation(async (input) => {
      const url = String(input);
      if (url.includes('/knowledge-graph/export'))
        return new Response(JSON.stringify({ detail: 'Unauthorized' }), { status: 401 });
      return new Response('{}', { status: 200 });
    });
    renderExplorer();

    expect(await screen.findByText(/couldn't load the knowledge graph/i)).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /retry/i })).toBeInTheDocument();
  });
});
