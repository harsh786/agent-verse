/**
 * Tests for InteractiveKnowledgeGraph — fetches GET /knowledge-graph/export and
 * renders the shared d3 `KnowledgeGraph`. The d3 component needs real layout
 * APIs jsdom lacks, so it is stubbed with a lightweight double that surfaces the
 * mapped node labels. We drive the loading / error / empty / data / refetch
 * branches through the mocked global fetch.
 */
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, beforeEach, describe, expect, test, vi } from 'vitest';
import { useAuthStore } from '@/stores/auth';

vi.mock('@/components/knowledge/KnowledgeGraph', () => ({
  KnowledgeGraph: ({ data }: { data: { nodes: Array<{ id: string; label: string }> } }) => (
    <div data-testid="kg-canvas">
      {data.nodes.map((n) => (
        <span key={n.id}>{n.label}</span>
      ))}
    </div>
  ),
}));

import { InteractiveKnowledgeGraph } from './InteractiveKnowledgeGraph';

const GRAPH = {
  nodes: [
    { node_id: 'n1', label: 'Payment Service', node_type: 'entity' },
    { node_id: 'n2', label: 'Refund Flow', node_type: 'concept' },
  ],
  edges: [{ edge_id: 'e1', source: 'n1', target: 'n2', edge_type: 'relates_to' }],
};

function mockFetch(opts: { graph?: unknown; status?: number } = {}) {
  return vi.spyOn(globalThis, 'fetch').mockImplementation(async (input) => {
    const url = String(input);
    if (url.includes('/knowledge-graph/export'))
      return new Response(JSON.stringify(opts.graph ?? GRAPH), {
        status: opts.status ?? 200,
        headers: { 'Content-Type': 'application/json' },
      });
    return new Response('{}', { status: 200, headers: { 'Content-Type': 'application/json' } });
  });
}

function renderGraph() {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={qc}>
      <InteractiveKnowledgeGraph height={400} />
    </QueryClientProvider>,
  );
}

beforeEach(() => {
  sessionStorage.clear();
  localStorage.clear();
  useAuthStore.setState({ apiKey: 'k', tenantId: 't', plan: 'free', isAuthenticated: true });
});
afterEach(() => vi.restoreAllMocks());

describe('InteractiveKnowledgeGraph', () => {
  test('renders the graph canvas with mapped node labels once data loads', async () => {
    mockFetch();
    renderGraph();
    expect(await screen.findByTestId('kg-canvas')).toBeInTheDocument();
    expect(screen.getByText('Payment Service')).toBeInTheDocument();
    expect(screen.getByText('Refund Flow')).toBeInTheDocument();
  });

  test('shows the empty state when the graph has no nodes', async () => {
    mockFetch({ graph: { nodes: [], edges: [] } });
    renderGraph();
    expect(await screen.findByText(/No graph yet/i)).toBeInTheDocument();
    expect(screen.queryByTestId('kg-canvas')).not.toBeInTheDocument();
  });

  test('shows an error state with a Retry action when the request fails', async () => {
    mockFetch({ status: 500 });
    renderGraph();
    expect(await screen.findByText(/Failed to load the graph/i)).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /Retry/i })).toBeInTheDocument();
  });

  test('shows a loading state (no canvas, no empty copy) while the request is in flight', () => {
    vi.spyOn(globalThis, 'fetch').mockReturnValue(new Promise(() => {}) as Promise<Response>);
    renderGraph();
    expect(screen.queryByTestId('kg-canvas')).not.toBeInTheDocument();
    expect(screen.queryByText(/No graph yet/i)).not.toBeInTheDocument();
    // The refresh affordance is always present.
    expect(screen.getByRole('button', { name: /Refresh graph/i })).toBeInTheDocument();
  });

  test('clicking Refresh refetches the exported graph', async () => {
    const spy = mockFetch();
    renderGraph();
    await screen.findByTestId('kg-canvas');
    const before = spy.mock.calls.filter(([u]) => String(u).includes('/knowledge-graph/export')).length;
    await userEvent.click(screen.getByRole('button', { name: /Refresh graph/i }));
    await waitFor(() =>
      expect(
        spy.mock.calls.filter(([u]) => String(u).includes('/knowledge-graph/export')).length,
      ).toBeGreaterThan(before),
    );
  });
});
