/**
 * Tests for GoalDNAPage — the execution-graph visualizer.
 *
 * @xyflow/react needs a real DOM/canvas jsdom can't provide, so it is stubbed
 * with a lightweight double that renders each node as a clickable button and
 * forwards onNodeClick. This lets us exercise the page's real branch logic
 * (loading / error / empty / graph), stats, node inspector and timeline while
 * leaving graph rendering to the (unrelated) library.
 */
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { render, screen, fireEvent, waitFor } from '@testing-library/react';
import React from 'react';
import { MemoryRouter, Route, Routes } from 'react-router-dom';
import { afterEach, beforeEach, describe, expect, test, vi } from 'vitest';
import { useAuthStore } from '@/stores/auth';

vi.mock('@xyflow/react', () => ({
  ReactFlow: ({ nodes, onNodeClick, children }: { nodes?: Array<{ id: string; data: { label?: string } }>; onNodeClick?: (e: unknown, n: unknown) => void; children?: React.ReactNode }) => (
    <div data-testid="react-flow">
      {(nodes ?? []).map((n) => (
        <button key={n.id} data-testid={`rf-node-${n.id}`} onClick={(e) => onNodeClick?.(e, n)}>
          {n.data?.label ?? n.id}
        </button>
      ))}
      {children}
    </div>
  ),
  ReactFlowProvider: ({ children }: { children?: React.ReactNode }) => <>{children}</>,
  Background: () => null,
  BackgroundVariant: { Dots: 'dots', Lines: 'lines', Cross: 'cross' },
  MiniMap: () => null,
  Panel: ({ children }: { children?: React.ReactNode }) => <div>{children}</div>,
  MarkerType: { ArrowClosed: 'arrowclosed' },
  useReactFlow: () => ({ fitView: vi.fn(), zoomIn: vi.fn(), zoomOut: vi.fn(), getNodes: () => [] }),
  getNodesBounds: () => ({ x: 0, y: 0, width: 200, height: 200 }),
}));

import { GoalDNAPage } from './GoalDNAPage';

const GRAPH = {
  goal_id: 'goal-dna-1',
  nodes: [
    { id: 'start', type: 'start', label: 'Start', data: {} },
    { id: 'step-1', type: 'step', label: 'List Files', data: {} },
    { id: 'tool-1', type: 'tool', label: 'list_dir', data: { tool_name: 'list_dir', server_id: 'srv-1', status: 'success', duration_ms: 1500, output_preview: 'file-A\nfile-B' } },
    { id: 'tool-2', type: 'tool', label: 'deploy', data: { tool_name: 'deploy', status: 'failed', error: 'deploy exploded' } },
    { id: 'end', type: 'end', label: 'Complete', data: {} },
  ],
  edges: [
    { id: 'e1', source: 'start', target: 'step-1' },
    { id: 'e2', source: 'step-1', target: 'tool-1' },
    { id: 'e3', source: 'tool-1', target: 'tool-2' },
    { id: 'e4', source: 'tool-2', target: 'end' },
  ],
  stats: { total_nodes: 5, total_edges: 4, tool_calls: 2, unique_tools: 2 },
};

function mockFetch(opts: { graph?: unknown; graphStatus?: number } = {}) {
  return vi.spyOn(globalThis, 'fetch').mockImplementation(async (input) => {
    const url = String(input);
    const json = (payload: unknown, status = 200) =>
      new Response(JSON.stringify(payload), { status, headers: { 'Content-Type': 'application/json' } });
    if (url.includes('/insights/graph/'))
      return json(opts.graph ?? GRAPH, opts.graphStatus ?? 200);
    if (url.includes('/goals/'))
      return json({ id: 'goal-dna-1', goal: 'do it', status: 'completed' });
    return json({});
  });
}

function renderPage(goalId = 'goal-dna-1') {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <MemoryRouter initialEntries={[`/goals/${goalId}/dna`]}>
      <QueryClientProvider client={qc}>
        <Routes>
          <Route path="/goals/:goalId/dna" element={<GoalDNAPage />} />
        </Routes>
      </QueryClientProvider>
    </MemoryRouter>,
  );
}

beforeEach(() => {
  sessionStorage.clear();
  localStorage.clear();
  useAuthStore.setState({ apiKey: 'k', tenantId: 't', plan: 'professional', isAuthenticated: true });
});
afterEach(() => vi.restoreAllMocks());

describe('GoalDNAPage', () => {
  test('renders the header with the Goal DNA title and truncated goal id', async () => {
    mockFetch();
    renderPage();
    expect(screen.getByText('Goal DNA')).toBeInTheDocument();
    expect(screen.getByText(/goal-dna-1/)).toBeInTheDocument();
    await screen.findByTestId('react-flow');
  });

  test('shows a loading state (no graph, no error) while the request is in flight', () => {
    vi.spyOn(globalThis, 'fetch').mockReturnValue(new Promise(() => {}) as Promise<Response>);
    renderPage();
    expect(screen.getByText('Goal DNA')).toBeInTheDocument();
    expect(screen.queryByTestId('react-flow')).not.toBeInTheDocument();
    expect(screen.queryByText(/Could not load execution graph/i)).not.toBeInTheDocument();
  });

  test('renders an error state with a Retry action when the graph request fails', async () => {
    mockFetch({ graphStatus: 500 });
    renderPage();
    expect(await screen.findByText(/Could not load execution graph/i)).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /Retry/i })).toBeInTheDocument();
  });

  test('renders an empty state when the graph has no nodes', async () => {
    mockFetch({ graph: { goal_id: 'goal-dna-1', nodes: [], edges: [], stats: { total_nodes: 0, total_edges: 0, tool_calls: 0, unique_tools: 0 } } });
    renderPage();
    expect(await screen.findByText(/No execution events yet/i)).toBeInTheDocument();
    expect(screen.queryByTestId('react-flow')).not.toBeInTheDocument();
  });

  test('renders the graph canvas and the stats strip once data loads', async () => {
    mockFetch();
    renderPage();
    expect(await screen.findByTestId('react-flow')).toBeInTheDocument();
    expect(screen.getByText('nodes')).toBeInTheDocument();
    expect(screen.getByText('tool calls')).toBeInTheDocument();
    expect(screen.getByText('unique tools')).toBeInTheDocument();
    // tool-2 failed → the failed stat pill appears.
    expect(screen.getByText('failed')).toBeInTheDocument();
  });

  test('clicking a tool node opens the inspector with its tool + server + output details', async () => {
    mockFetch();
    renderPage();
    fireEvent.click(await screen.findByTestId('rf-node-tool-1'));
    expect(await screen.findByText('srv-1')).toBeInTheDocument();
    expect(screen.getByText(/Output preview/i)).toBeInTheDocument();
    expect(screen.getByText('Server')).toBeInTheDocument();
  });

  test('the inspector surfaces the error for a failed tool node', async () => {
    mockFetch();
    renderPage();
    fireEvent.click(await screen.findByTestId('rf-node-tool-2'));
    expect(await screen.findByText('deploy exploded')).toBeInTheDocument();
    expect(screen.getByText('Error')).toBeInTheDocument();
  });

  test('toggling the Timeline reveals the execution timeline sidebar', async () => {
    mockFetch();
    renderPage();
    await screen.findByTestId('react-flow');
    fireEvent.click(screen.getByRole('button', { name: /Timeline/i }));
    expect(await screen.findByText(/Execution Timeline/i)).toBeInTheDocument();
  });

  test('the Refresh button refetches the execution graph', async () => {
    const spy = mockFetch();
    renderPage();
    await screen.findByTestId('react-flow');
    const graphCallsBefore = spy.mock.calls.filter(([u]) => String(u).includes('/insights/graph/')).length;
    fireEvent.click(screen.getAllByRole('button', { name: /Refresh/i })[0]);
    await waitFor(() =>
      expect(spy.mock.calls.filter(([u]) => String(u).includes('/insights/graph/')).length).toBeGreaterThan(graphCallsBefore),
    );
  });
});
