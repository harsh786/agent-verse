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

const REAL_NODE_DETAIL = {
  node: {
    node_id: 'n1', node_type: 'document', label: 'Runbook.md', content: 'Full runbook contents.',
    confidence: 0.95, source_id: 'doc-1', metadata: {},
  },
  edges: [
    { edge_id: 'e1', edge_type: 'mentions', source_node_id: 'n1', target_node_id: 'n2', label: 'mentions', confidence: 0.9, evidence: '' },
  ],
};

function mockFetch(payload: unknown, opts?: { tasks?: unknown; missions?: unknown; events?: unknown; nodeDetail?: unknown; nodeDetailStatus?: number; tasksStatus?: number; mapsStatus?: number; eventsStatus?: number }) {
  return vi.spyOn(globalThis, 'fetch').mockImplementation(async (input) => {
    const url = String(input);
    if (url.includes('/knowledge-graph/nodes/'))
      return new Response(JSON.stringify(opts?.nodeDetail ?? REAL_NODE_DETAIL), { status: opts?.nodeDetailStatus ?? 200, headers: { 'Content-Type': 'application/json' } });
    if (url.includes('/knowledge-graph/export'))
      return new Response(JSON.stringify(payload), { status: opts?.mapsStatus ?? 200, headers: { 'Content-Type': 'application/json' } });
    if (url.includes('/tasks'))
      return new Response(JSON.stringify(opts?.tasks ?? { data: [], cursor: null, hasMore: false }), { status: opts?.tasksStatus ?? 200, headers: { 'Content-Type': 'application/json' } });
    if (url.includes('/missions'))
      return new Response(JSON.stringify(opts?.missions ?? { data: [], cursor: null, hasMore: false }), { status: 200, headers: { 'Content-Type': 'application/json' } });
    if (url.includes('/events'))
      return new Response(JSON.stringify(opts?.events ?? { data: [], cursor: null, hasMore: false }), { status: opts?.eventsStatus ?? 200, headers: { 'Content-Type': 'application/json' } });
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

/** Waits for the force-directed graph's node circles to be appended to the SVG
 *  (KnowledgeGraph builds them via an async d3-force import). Scoped to the
 *  "Knowledge graph view" panel so it never matches unrelated icon <svg><circle>
 *  markup elsewhere on the page (e.g. the Timeline tab icon). */
async function findGraphNodeCircles(): Promise<SVGCircleElement[]> {
  return waitFor(() => {
    const panel = screen.getByLabelText('Knowledge graph view');
    const circles = panel.querySelectorAll('svg circle');
    expect(circles.length).toBeGreaterThan(0);
    return Array.from(circles) as SVGCircleElement[];
  });
}

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
    const retryBtn = screen.getByRole('button', { name: /retry/i });
    expect(retryBtn).toBeInTheDocument();

    // Clicking retry re-invokes the query's refetch (exercises the onClick handler).
    fireEvent.click(retryBtn);
    expect(await screen.findByText(/couldn't load the knowledge graph/i)).toBeInTheDocument();
  });

  test('clicking a node opens the detail panel with content and connections, then closes it', async () => {
    mockFetch(REAL_GRAPH_PAYLOAD);
    renderExplorer();
    await screen.findByLabelText('Knowledge graph view');

    const circles = await findGraphNodeCircles();
    fireEvent.click(circles[0]);

    expect(await screen.findByLabelText('Node detail')).toBeInTheDocument();
    expect(await screen.findByText('Full runbook contents.')).toBeInTheDocument();
    expect(screen.getByText(/connections \(1\)/i)).toBeInTheDocument();

    const closeBtn = screen.getByLabelText('Close node detail');
    fireEvent.click(closeBtn);
    // The panel unmounts once its AnimatePresence exit transition finishes.
    await waitFor(() => expect(screen.queryByLabelText('Node detail')).not.toBeInTheDocument(), { timeout: 3000 });
  });

  test('node detail panel shows a fallback message when the node detail request fails', async () => {
    mockFetch(REAL_GRAPH_PAYLOAD, { nodeDetailStatus: 500 });
    renderExplorer();
    await screen.findByLabelText('Knowledge graph view');

    const circles = await findGraphNodeCircles();
    fireEvent.click(circles[0]);

    expect(await screen.findByText(/could not load node detail/i)).toBeInTheDocument();
  });

  test('toggling type filter chips narrows visible nodes, and toggling to zero shows the no-match state', async () => {
    mockFetch(REAL_GRAPH_PAYLOAD);
    renderExplorer();
    await screen.findByLabelText('Knowledge graph view');

    const documentChips = screen.getAllByRole('button', { name: 'document' });
    const entityChips = screen.getAllByRole('button', { name: 'entity' });
    const conceptChips = screen.getAllByRole('button', { name: 'concept' });

    // Deselect all three real node types one at a time (toggleType branch coverage).
    fireEvent.click(documentChips[0]);
    fireEvent.click(entityChips[0]);
    fireEvent.click(conceptChips[0]);

    expect(await screen.findByText(/no nodes match the selected types/i)).toBeInTheDocument();

    // Re-selecting every type collapses back to the "all" (null) state.
    fireEvent.click(documentChips[0]);
    fireEvent.click(entityChips[0]);
    fireEvent.click(conceptChips[0]);
    expect(screen.queryByText(/no nodes match the selected types/i)).not.toBeInTheDocument();
  });
});

describe('ObsidianVaultExplorer — Files tab (same real KG nodes, grouped by type)', () => {
  test('groups real nodes by type, expands a group, and filters via search', async () => {
    mockFetch(REAL_GRAPH_PAYLOAD);
    renderExplorer();
    await screen.findByLabelText('Knowledge graph view');
    fireEvent.click(screen.getByRole('tab', { name: /files/i }));

    // Group headers derived from real node types, with real per-type counts.
    expect(await screen.findByText('document (1)')).toBeInTheDocument();
    expect(screen.getByText('entity (1)')).toBeInTheDocument();
    expect(screen.getByText('concept (1)')).toBeInTheDocument();

    // Expand the "document" group to reveal its node and confidence percentage.
    const groupToggle = screen.getByRole('button', { name: /expand document group/i });
    fireEvent.click(groupToggle);
    expect(await screen.findByText('Runbook.md')).toBeInTheDocument();
    expect(screen.getByText('95%')).toBeInTheDocument();
    expect(groupToggle).toHaveAttribute('aria-expanded', 'true');

    // Collapse it again — aria-expanded flips back synchronously even while the
    // AnimatePresence exit transition finishes unmounting the panel.
    fireEvent.click(screen.getByRole('button', { name: /collapse document group/i }));
    expect(screen.getByRole('button', { name: /expand document group/i })).toHaveAttribute('aria-expanded', 'false');

    // Search narrows the node list and its derived per-type groups.
    fireEvent.change(screen.getByLabelText('Search vault notes'), { target: { value: 'agentverse' } });
    expect(await screen.findByText('entity (1)')).toBeInTheDocument();
    expect(screen.queryByText('document (1)')).not.toBeInTheDocument();
  });

  test('shows an honest empty state when the knowledge graph has no nodes', async () => {
    mockFetch({ tenant_id: 'tenant-1', exported_at: '2026-09-01T00:00:00Z', nodes: [], edges: [], stats: { nodes: 0, edges: 0 }, format: 'agentverse_kg_v1' });
    renderExplorer();
    fireEvent.click(screen.getByRole('tab', { name: /files/i }));

    expect(await screen.findByText(/no knowledge graph nodes yet/i)).toBeInTheDocument();
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

  test('shows a retry option on a failed tasks fetch', async () => {
    mockFetch(REAL_GRAPH_PAYLOAD, { tasksStatus: 500 });
    renderExplorer();

    await screen.findByLabelText('Knowledge graph view');
    fireEvent.click(screen.getByRole('tab', { name: /bases/i }));

    expect(await screen.findByText(/couldn't load tasks/i)).toBeInTheDocument();
    const retryBtn = screen.getByRole('button', { name: /retry/i });
    fireEvent.click(retryBtn);
    expect(await screen.findByText(/couldn't load tasks/i)).toBeInTheDocument();
  });

  test('renders every priority and status color variant across mixed rows', async () => {
    const mixedTasks = {
      data: [
        { id: 't1', tenant_id: 'tenant-1', org_id: 'org-1', mission_id: 'm1', title: 'Critical fix', objective: '', status: 'failed', priority: 'critical', assigned_to: 'agent-1', created_at: '2026-08-01T00:00:00Z', updated_at: '2026-08-01T00:00:00Z' },
        { id: 't2', tenant_id: 'tenant-1', org_id: 'org-1', mission_id: 'm1', title: 'Routine task', objective: '', status: 'queued', priority: 'medium', assigned_to: null, created_at: '2026-08-01T00:00:00Z', updated_at: '2026-08-01T00:00:00Z' },
        { id: 't3', tenant_id: 'tenant-1', org_id: 'org-1', mission_id: 'm1', title: 'Low priority', objective: '', status: 'unknown_status', priority: 'low', assigned_to: 'agent-2', created_at: '2026-08-01T00:00:00Z', updated_at: '2026-08-01T00:00:00Z' },
      ],
      cursor: null, hasMore: false,
    };
    mockFetch(REAL_GRAPH_PAYLOAD, { tasks: mixedTasks });
    renderExplorer();

    await screen.findByLabelText('Knowledge graph view');
    fireEvent.click(screen.getByRole('tab', { name: /bases/i }));

    expect(await screen.findByText('Critical fix')).toBeInTheDocument();
    expect(screen.getByText('failed')).toBeInTheDocument();
    expect(screen.getByText('critical')).toBeInTheDocument();
    expect(screen.getByText('queued')).toBeInTheDocument();
    expect(screen.getByText('medium')).toBeInTheDocument();
    expect(screen.getByText('unknown status')).toBeInTheDocument();
    expect(screen.getByText('low')).toBeInTheDocument();
    // Missing owner falls back to the em-dash placeholder.
    expect(screen.getAllByText('—').length).toBeGreaterThan(0);
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

  test('shows a retry option on a failed map fetch (no fabricated fallback data)', async () => {
    mockFetch(REAL_GRAPH_PAYLOAD, { mapsStatus: 500 });
    renderExplorer();

    fireEvent.click(screen.getByRole('tab', { name: /maps/i }));

    expect(await screen.findByText(/couldn't load the knowledge map/i)).toBeInTheDocument();
    const retryBtn = screen.getByRole('button', { name: /retry/i });
    fireEvent.click(retryBtn);
    expect(await screen.findByText(/couldn't load the knowledge map/i)).toBeInTheDocument();
  });

  test('renders a single cluster/relationship count in singular form, with an unknown type falling back to the neutral color', async () => {
    const singleTypePayload = {
      tenant_id: 'tenant-1', exported_at: '2026-09-01T00:00:00Z',
      nodes: [
        { node_id: 'n1', node_type: 'mystery_type', label: 'Odd node', confidence: 0.5, source_id: null },
        { node_id: 'n2', node_type: 'mystery_type', label: 'Odd node 2', confidence: 0.5, source_id: null },
      ],
      edges: [{ edge_id: 'e1', edge_type: 'mentions', source: 'n1', target: 'n2', confidence: 0.5 }],
      stats: { nodes: 2, edges: 1 }, format: 'agentverse_kg_v1',
    };
    mockFetch(singleTypePayload);
    renderExplorer();

    fireEvent.click(screen.getByRole('tab', { name: /maps/i }));

    expect(await screen.findByLabelText('Knowledge map — clustered by node type')).toBeInTheDocument();
    // Same-type edge is excluded from cross-type link counts (linkCounts `continue` branch),
    // so the summary reads "1 node type" and "1 relationship" in singular form.
    const summary = await screen.findByText((_, el) => el?.tagName === 'P' && /1 node type/.test(el.textContent ?? '') && /1 relationship/.test(el.textContent ?? ''));
    expect(summary).toBeInTheDocument();
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

  test('shows a retry option on a failed events fetch', async () => {
    mockFetch(REAL_GRAPH_PAYLOAD, { eventsStatus: 500 });
    renderExplorer();

    await screen.findByLabelText('Knowledge graph view');
    fireEvent.click(screen.getByRole('tab', { name: /timeline/i }));

    expect(await screen.findByText(/couldn't load org activity/i)).toBeInTheDocument();
    const retryBtn = screen.getByRole('button', { name: /retry/i });
    fireEvent.click(retryBtn);
    expect(await screen.findByText(/couldn't load org activity/i)).toBeInTheDocument();
  });

  test('ages every event using the correct relative-time bucket, severity dot, and event_type title fallback', async () => {
    const now = Date.now();
    const iso = (msAgo: number) => new Date(now - msAgo).toISOString();
    const mixedEvents = {
      data: [
        { id: 'ev-now', org_id: 'org-1', event_type: 'org.now', title: '', description: '', severity: 'info', entity_type: 'mission', entity_id: 'm1', created_at: iso(0) },
        { id: 'ev-mins', org_id: 'org-1', event_type: 'org.mins', title: 'Minutes old', description: '', severity: 'critical', entity_type: 'mission', entity_id: 'm1', created_at: iso(5 * 60_000) },
        { id: 'ev-hours', org_id: 'org-1', event_type: 'org.hours', title: 'Hours old', description: '', severity: 'error', entity_type: 'mission', entity_id: 'm1', created_at: iso(5 * 3_600_000) },
        { id: 'ev-days', org_id: 'org-1', event_type: 'org.days', title: 'Days old', description: '', severity: 'warning', entity_type: 'mission', entity_id: 'm1', created_at: iso(5 * 86_400_000) },
        { id: 'ev-months', org_id: 'org-1', event_type: 'org.months', title: 'Months old', description: '', severity: 'info', entity_type: 'mission', entity_id: 'm1', created_at: iso(60 * 86_400_000) },
        { id: 'ev-years', org_id: 'org-1', event_type: 'org.years', title: 'Years old', description: '', severity: 'info', entity_type: 'mission', entity_id: 'm1', created_at: iso(400 * 86_400_000) },
        { id: 'ev-bad', org_id: 'org-1', event_type: 'org.bad', title: 'Bad date', description: '', severity: 'info', entity_type: 'mission', entity_id: 'm1', created_at: 'not-a-date' },
      ],
      cursor: null, hasMore: false,
    };
    mockFetch(REAL_GRAPH_PAYLOAD, { events: mixedEvents });
    renderExplorer();

    await screen.findByLabelText('Knowledge graph view');
    fireEvent.click(screen.getByRole('tab', { name: /timeline/i }));

    // Empty title falls back to the raw event_type (line 716 `e.title || e.event_type`).
    expect(await screen.findByText('org.now')).toBeInTheDocument();
    expect(screen.getByText('Minutes old')).toBeInTheDocument();
    expect(screen.getByText('Hours old')).toBeInTheDocument();
    expect(screen.getByText('Days old')).toBeInTheDocument();
    expect(screen.getByText('Months old')).toBeInTheDocument();
    expect(screen.getByText('Years old')).toBeInTheDocument();
    expect(screen.getByText('Bad date')).toBeInTheDocument();

    expect(screen.getByText('now')).toBeInTheDocument();
    expect(screen.getByText('5m')).toBeInTheDocument();
    expect(screen.getByText('5h')).toBeInTheDocument();
    expect(screen.getByText('5d')).toBeInTheDocument();
    expect(screen.getByText('2mo')).toBeInTheDocument();
    expect(screen.getByText('1y')).toBeInTheDocument();
    // An unparseable date renders the em-dash fallback, and appears at least
    // once (also used by the invalid-date row's age).
    expect(screen.getAllByText('—').length).toBeGreaterThan(0);
  });
});
