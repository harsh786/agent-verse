import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { render, screen, waitFor } from '@testing-library/react';
import React from 'react';
import { afterEach, beforeEach, describe, expect, test, vi } from 'vitest';
import { useAuthStore } from '@/stores/auth';

// @xyflow/react needs a real DOM + canvas jsdom can't provide; stub it so the
// component's branch logic (loading / empty / graph) is what we exercise.
vi.mock('@xyflow/react', () => ({
  ReactFlow: ({ children }: { children?: React.ReactNode }) => <div data-testid="react-flow">{children}</div>,
  ReactFlowProvider: ({ children }: { children?: React.ReactNode }) => <>{children}</>,
  Background: () => null,
  Controls: () => null,
  Handle: () => null,
  Position: { Top: 'top', Bottom: 'bottom' },
  MarkerType: { ArrowClosed: 'arrowclosed' },
  useNodesState: (nodes: unknown[]) => [nodes, vi.fn(), vi.fn()],
  useEdgesState: (edges: unknown[]) => [edges, vi.fn(), vi.fn()],
}));

import { MissionGraph } from './MissionGraph';

function mockFetch(tasks: unknown[] = []) {
  return vi.spyOn(globalThis, 'fetch').mockImplementation(async (input) => {
    const url = String(input);
    if (url.includes('/tasks'))
      return new Response(JSON.stringify({ data: tasks }), { status: 200, headers: { 'Content-Type': 'application/json' } });
    return new Response('{}', { status: 200, headers: { 'Content-Type': 'application/json' } });
  });
}

function renderGraph() {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={qc}>
      <MissionGraph orgId="o1" missionId="m1" />
    </QueryClientProvider>,
  );
}

beforeEach(() => {
  sessionStorage.clear(); localStorage.clear();
  useAuthStore.setState({ apiKey: 'k', tenantId: 't', plan: 'free', isAuthenticated: true });
});
afterEach(() => vi.restoreAllMocks());

describe('MissionGraph', () => {
  test('shows a loading indicator while tasks are being fetched', () => {
    vi.spyOn(globalThis, 'fetch').mockImplementation(() => new Promise(() => {}));
    renderGraph();
    expect(screen.getByLabelText('Loading task graph')).toBeInTheDocument();
  });

  test('renders an empty state when the mission has no tasks', async () => {
    mockFetch([]);
    renderGraph();
    expect(await screen.findByText('No tasks in this mission yet.')).toBeInTheDocument();
    expect(screen.queryByTestId('react-flow')).not.toBeInTheDocument();
  });

  test('renders the React Flow canvas once tasks exist and queries by mission id', async () => {
    const spy = mockFetch([
      { id: 't1', title: 'Design schema', status: 'running' },
      { id: 't2', title: 'Write migration', status: 'planned' },
    ]);
    renderGraph();
    expect(await screen.findByTestId('react-flow')).toBeInTheDocument();
    expect(screen.queryByText('No tasks in this mission yet.')).not.toBeInTheDocument();
    // The tasks query carries the selected mission id.
    expect(spy.mock.calls.some(([u]) => String(u).includes('/tasks') && String(u).includes('mission_id=m1'))).toBe(true);
    await waitFor(() => expect(spy).toHaveBeenCalled());
  });
});
