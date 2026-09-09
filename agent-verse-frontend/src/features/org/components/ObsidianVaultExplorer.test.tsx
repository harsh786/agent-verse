import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
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

const REAL_TASKS_PAGE = {
  data: [
    { id: 't1', tenant_id: 'tenant-1', org_id: 'org-1', mission_id: 'm1', title: 'Draft Q3 report', objective: '', status: 'running', priority: 'high', assigned_to: 'agent-writer-1', created_at: '2026-08-01T00:00:00Z', updated_at: '2026-08-01T00:00:00Z' },
  ],
  cursor: null, hasMore: false,
};
const REAL_MISSIONS_PAGE = {
  data: [
    { id: 'm1', tenant_id: 'tenant-1', org_id: 'org-1', dept_id: null, assigned_team_id: 'team-1', title: 'Launch Q3 campaign', objective: '', why: '', expected_outcome: '', status: 'active', priority: 'high', source: 'user', autonomy_level: 2, budget_usd: null, deadline: null, tags: [], created_by: null, outputs: [], evidence: [], started_at: null, completed_at: null, created_at: '2026-08-01T00:00:00Z', updated_at: '2026-08-01T00:00:00Z' },
  ],
  cursor: null, hasMore: false,
};
const REAL_EVENTS_PAGE = {
  data: [
    { id: 'ev1', org_id: 'org-1', event_type: 'org.mission.created', title: 'Mission created', description: '', severity: 'info', entity_type: 'mission', entity_id: 'm1', created_at: '2026-08-01T00:00:00Z' },
  ],
  cursor: null, hasMore: false,
};

function mockFetch(payload: unknown, opts?: { tasks?: unknown; missions?: unknown; events?: unknown }) {
  return vi.spyOn(globalThis, 'fetch').mockImplementation(async (input) => {
    const url = String(input);
    if (url.includes('/knowledge-graph/export'))
      return new Response(JSON.stringify(payload), { status: 200, headers: { 'Content-Type': 'application/json' } });
    if (url.includes('/tasks'))
      return new Response(JSON.stringify(opts?.tasks ?? { data: [], cursor: null, hasMore: false }), { status: 200, headers: { 'Content-Type': 'application/json' } });
    if (url.includes('/missions'))
      return new Response(JSON.stringify(opts?.missions ?? { data: [], cursor: null, hasMore: false }), { status: 200, headers: { 'Content-Type': 'application/json' } });
    if (url.includes('/events'))
      return new Response(JSON.stringify(opts?.events ?? { data: [], cursor: null, hasMore: false }), { status: 200, headers: { 'Content-Type': 'application/json' } });
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

describe('ObsidianVaultExplorer — Bases tab (real org tasks/missions)', () => {
  test('renders real task rows (title, status, priority, owner, age) from /v1/org/{id}/tasks', async () => {
    mockFetch(REAL_GRAPH_PAYLOAD, { tasks: REAL_TASKS_PAGE });
    renderExplorer();

    await screen.findByLabelText('Knowledge graph view');
    fireEvent.click(screen.getByRole('tab', { name: /bases/i }));

    expect(await screen.findByText('Draft Q3 report')).toBeInTheDocument();
    expect(screen.getByText('running')).toBeInTheDocument();
    expect(screen.getByText('agent-writer-1')).toBeInTheDocument();
    // No fabricated demo rows.
    expect(screen.queryByText('Q3 Revenue Analysis')).not.toBeInTheDocument();
  });

  test('switching to Missions renders real mission rows from /v1/org/{id}/missions', async () => {
    mockFetch(REAL_GRAPH_PAYLOAD, { tasks: { data: [], cursor: null, hasMore: false }, missions: REAL_MISSIONS_PAGE });
    renderExplorer();

    await screen.findByLabelText('Knowledge graph view');
    fireEvent.click(screen.getByRole('tab', { name: /bases/i }));
    // Wait for the Bases panel to finish mounting (AnimatePresence mode="wait")
    // before interacting with its toggle.
    await screen.findByText(/no tasks yet/i);
    fireEvent.click(screen.getByRole('button', { name: /^missions$/i }));

    expect(await screen.findByText('Launch Q3 campaign')).toBeInTheDocument();
    expect(screen.getByText('team-1')).toBeInTheDocument();
  });

  test('shows an honest empty state when the org has no tasks', async () => {
    mockFetch(REAL_GRAPH_PAYLOAD, { tasks: { data: [], cursor: null, hasMore: false } });
    renderExplorer();

    await screen.findByLabelText('Knowledge graph view');
    fireEvent.click(screen.getByRole('tab', { name: /bases/i }));

    expect(await screen.findByText(/no tasks yet/i)).toBeInTheDocument();
  });
});

describe('ObsidianVaultExplorer — Maps tab (real KG reused as a cluster view)', () => {
  test('renders a clustered map derived from the same real /knowledge-graph/export payload', async () => {
    mockFetch(REAL_GRAPH_PAYLOAD);
    renderExplorer();

    await screen.findByLabelText('Knowledge graph view');
    fireEvent.click(screen.getByRole('tab', { name: /maps/i }));

    expect(await screen.findByLabelText('Knowledge map — clustered by node type')).toBeInTheDocument();
    // Cluster labels come straight from the real node types in the payload.
    expect(screen.getAllByText('document').length).toBeGreaterThan(0);
    expect(screen.getAllByText('entity').length).toBeGreaterThan(0);
  });

  test('shows an honest empty state when the tenant knowledge graph has no nodes', async () => {
    mockFetch({ tenant_id: 'tenant-1', exported_at: '2026-09-01T00:00:00Z', nodes: [], edges: [], stats: { nodes: 0, edges: 0 }, format: 'agentverse_kg_v1' });
    renderExplorer();

    fireEvent.click(screen.getByRole('tab', { name: /maps/i }));

    expect(await screen.findByText(/knowledge map is empty/i)).toBeInTheDocument();
  });
});

describe('ObsidianVaultExplorer — Timeline tab (real org event history)', () => {
  test('renders real event rows from /v1/org/{id}/events, never a fabricated sparkline', async () => {
    mockFetch(REAL_GRAPH_PAYLOAD, { events: REAL_EVENTS_PAGE });
    renderExplorer();

    await screen.findByLabelText('Knowledge graph view');
    fireEvent.click(screen.getByRole('tab', { name: /timeline/i }));

    expect(await screen.findByText('Mission created')).toBeInTheDocument();
    expect(screen.getByLabelText(/org activity by day/i)).toBeInTheDocument();
    expect(screen.queryByText('notes this week')).not.toBeInTheDocument();
  });

  test('shows an honest empty state when the org has no event history', async () => {
    mockFetch(REAL_GRAPH_PAYLOAD, { events: { data: [], cursor: null, hasMore: false } });
    renderExplorer();

    await screen.findByLabelText('Knowledge graph view');
    fireEvent.click(screen.getByRole('tab', { name: /timeline/i }));

    expect(await screen.findByText(/no activity yet/i)).toBeInTheDocument();
  });
});
