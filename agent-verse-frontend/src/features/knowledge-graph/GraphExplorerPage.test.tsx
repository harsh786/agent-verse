/**
 * Tests for GraphExplorerPage — the knowledge-graph explorer. Exercises the
 * stats/node list, node-detail panel, search + type filters, the extract flow,
 * empty state, and the list/graph view toggle. The embedded interactive graph
 * uses the d3 KnowledgeGraph, stubbed here; all data flows through fetch.
 */
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, beforeEach, describe, expect, test, vi } from 'vitest';
import { useAuthStore } from '@/stores/auth';

vi.mock('@/components/knowledge/KnowledgeGraph', () => ({
  KnowledgeGraph: () => <div data-testid="kg-canvas" />,
}));

import { GraphExplorerPage } from './GraphExplorerPage';

const NODES = {
  nodes: [
    {
      node_id: 'n1',
      label: 'Payment Service',
      node_type: 'entity',
      confidence: 0.9,
      content: 'Handles charges and refunds',
      metadata: { region: 'us-east' },
    },
    { node_id: 'n2', label: 'Refund Concept', node_type: 'concept', confidence: 0.5 },
  ],
};

const NODE_DETAIL = {
  edges: [
    { edge_id: 'ed1', edge_type: 'relates_to', source_node_id: 'n1', target_node_id: 'n2', confidence: 0.8 },
  ],
};

function mockFetch(opts: { nodes?: unknown } = {}) {
  return vi.spyOn(globalThis, 'fetch').mockImplementation(async (input, init) => {
    const url = String(input);
    const method = (init?.method ?? 'GET').toUpperCase();
    const json = (payload: unknown, status = 200) =>
      new Response(JSON.stringify(payload), { status, headers: { 'Content-Type': 'application/json' } });
    if (url.includes('/knowledge-graph/extract') && method === 'POST')
      return json({ entities_extracted: 3, relationships_extracted: 2 });
    if (url.includes('/knowledge-graph/stats')) return json({ total_nodes: 12, total_edges: 7 });
    if (url.includes('/knowledge-graph/export')) return json({ nodes: [], edges: [] });
    // Node detail: /knowledge-graph/nodes/<id> (has a segment after nodes/)
    if (/\/knowledge-graph\/nodes\/[^?]+/.test(url)) return json(NODE_DETAIL);
    if (url.includes('/knowledge-graph/nodes')) return json(opts.nodes ?? NODES);
    return json({});
  });
}

function renderPage() {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={qc}>
      <GraphExplorerPage />
    </QueryClientProvider>,
  );
}

beforeEach(() => {
  sessionStorage.clear();
  localStorage.clear();
  useAuthStore.setState({ apiKey: 'k', tenantId: 't', plan: 'free', isAuthenticated: true });
});
afterEach(() => vi.restoreAllMocks());

describe('GraphExplorerPage', () => {
  test('renders the header, stats and the node list with confidence', async () => {
    mockFetch();
    renderPage();
    expect(screen.getByRole('heading', { name: /Graph Explorer/i })).toBeInTheDocument();
    expect(await screen.findByText('Payment Service')).toBeInTheDocument();
    // Stats card total_nodes
    expect(screen.getByText('12')).toBeInTheDocument();
    // Node subtitle shows rounded confidence
    expect(screen.getByText(/90% confidence/i)).toBeInTheDocument();
  });

  test('shows the empty state when there are no nodes', async () => {
    mockFetch({ nodes: { nodes: [] } });
    renderPage();
    expect(await screen.findByText(/No nodes yet/i)).toBeInTheDocument();
    expect(screen.getByText(/Extract text to populate the graph/i)).toBeInTheDocument();
  });

  test('clicking a node opens the detail panel with content and connections', async () => {
    mockFetch();
    renderPage();
    await userEvent.click(await screen.findByText('Payment Service'));
    // Detail heading (h2)
    expect(await screen.findByRole('heading', { name: 'Payment Service', level: 2 })).toBeInTheDocument();
    expect(screen.getByText('Handles charges and refunds')).toBeInTheDocument();
    // Connections section from node detail
    expect(await screen.findByText(/Connections \(1\)/i)).toBeInTheDocument();
    expect(screen.getByText('relates_to')).toBeInTheDocument();
    // Metadata key
    expect(screen.getByText('region')).toBeInTheDocument();
  });

  test('typing in the search box issues a filtered nodes request', async () => {
    const spy = mockFetch();
    renderPage();
    await screen.findByText('Payment Service');
    await userEvent.type(screen.getByPlaceholderText(/Search nodes/i), 'Pay');
    await waitFor(() =>
      expect(
        spy.mock.calls.some(([u]) => String(u).includes('search=Pay')),
      ).toBe(true),
    );
  });

  test('choosing a node-type filter issues a typed nodes request', async () => {
    const spy = mockFetch();
    renderPage();
    await screen.findByText('Payment Service');
    await userEvent.click(screen.getByRole('button', { name: /^concept$/i }));
    await waitFor(() =>
      expect(
        spy.mock.calls.some(([u]) => String(u).includes('node_type=concept')),
      ).toBe(true),
    );
  });

  test('the extract panel posts text to the extract endpoint', async () => {
    const spy = mockFetch();
    renderPage();
    await screen.findByText('Payment Service');
    // Open the extract panel (the header "Extract" toggle).
    await userEvent.click(screen.getByRole('button', { name: /^Extract$/i }));
    await userEvent.type(
      screen.getByPlaceholderText(/Paste text to extract/i),
      'Alice manages the payment service',
    );
    // The panel's own Extract submit button.
    const panelExtract = screen.getAllByRole('button', { name: /^Extract$/i }).pop()!;
    await userEvent.click(panelExtract);
    await waitFor(() =>
      expect(
        spy.mock.calls.some(
          ([u, i]) => String(u).includes('/knowledge-graph/extract') && (i as RequestInit)?.method === 'POST',
        ),
      ).toBe(true),
    );
  });

  test('switching to the graph view renders the interactive graph canvas', async () => {
    mockFetch();
    renderPage();
    await screen.findByText('Payment Service');
    await userEvent.click(screen.getByRole('button', { name: /^graph$/i }));
    // The interactive graph wrapper exposes its Refresh affordance.
    expect(await screen.findByRole('button', { name: /Refresh graph/i })).toBeInTheDocument();
  });

  test('the node-type filter row exposes the All filter and known types', async () => {
    mockFetch();
    renderPage();
    await screen.findByText('Payment Service');
    const allBtn = screen.getByRole('button', { name: /^All$/i });
    expect(allBtn).toBeInTheDocument();
    expect(within(allBtn.parentElement as HTMLElement).getByRole('button', { name: /^entity$/i })).toBeInTheDocument();
  });
});
