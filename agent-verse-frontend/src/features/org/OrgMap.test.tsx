import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import React from 'react';
import { MemoryRouter } from 'react-router-dom';
import { afterEach, beforeEach, describe, expect, test, vi } from 'vitest';
import { useAuthStore } from '@/stores/auth';

// The real @xyflow/react engine measures the DOM (needs real layout/canvas that
// jsdom lacks), which spins its internal effects forever and hangs the runner.
// Mock it with a light stub that still renders each node's label — so OrgMap's
// own graph-building, toolbar, search and refresh behaviour is exercised for real.
// (Mirrors the existing @xyflow mock in CivilizationPage.test.tsx.)
vi.mock('@xyflow/react', () => ({
  // Render through the real per-type node components (DeptNode/AgentNode) when a
  // matching entry exists in `nodeTypes`, so their JSX is actually exercised —
  // still without the real engine's DOM-measuring layout machinery.
  ReactFlow: ({
    nodes, nodeTypes,
  }: {
    nodes?: Array<{ id: string; type?: string; data?: Record<string, unknown> }>;
    nodeTypes?: Record<string, React.ComponentType<{ data: Record<string, unknown> }>>;
  }) => (
    <div data-testid="react-flow">
      {(nodes ?? []).map((n) => {
        const Comp = n.type ? nodeTypes?.[n.type] : undefined;
        return (
          <div key={n.id}>
            {Comp ? <Comp data={n.data ?? {}} /> : String(n.data?.label ?? '')}
          </div>
        );
      })}
    </div>
  ),
  ReactFlowProvider: ({ children }: { children?: React.ReactNode }) => <>{children}</>,
  Background: () => null,
  Controls: () => null,
  // Invoke the real `nodeColor` callback with representative dept/agent nodes so
  // its branches are exercised, same spirit as feeding nodeTypes above.
  MiniMap: ({ nodeColor }: { nodeColor?: (n: { type: string; data: Record<string, unknown> }) => string }) => {
    nodeColor?.({ type: 'dept', data: { color: '#123456' } });
    nodeColor?.({ type: 'agent', data: { status: 'executing' } });
    nodeColor?.({ type: 'agent', data: { status: 'unknown-status' } });
    return null;
  },
  Handle: () => null,
  Position: { Top: 'top', Bottom: 'bottom', Left: 'left', Right: 'right' },
  MarkerType: { ArrowClosed: 'arrowclosed' },
  useNodesState: (nodes: unknown[]) => [nodes, vi.fn(), vi.fn()],
  useEdgesState: (edges: unknown[]) => [edges, vi.fn(), vi.fn()],
}));

// Imported after the mock is declared (vi.mock is hoisted above imports).
import { OrgMap } from './OrgMap';

const DEPARTMENTS = [
  { id: 'd1', org_id: 'org-1', name: 'Engineering', purpose: 'Build', capability_domains: [], parent_dept_id: null, manager_agent_id: null, agent_count: 4, status: 'active', created_at: '', updated_at: '' },
  { id: 'd2', org_id: 'org-1', name: 'Marketing', purpose: 'Grow', capability_domains: [], parent_dept_id: null, manager_agent_id: null, agent_count: 2, status: 'active', created_at: '', updated_at: '' },
];

function mockDepartments(list: unknown[] = DEPARTMENTS) {
  return vi.spyOn(globalThis, 'fetch').mockImplementation(async (input) => {
    const url = String(input);
    if (url.includes('/departments'))
      return new Response(JSON.stringify(list), { status: 200, headers: { 'Content-Type': 'application/json' } });
    return new Response('{}', { status: 200, headers: { 'Content-Type': 'application/json' } });
  });
}

function renderMap() {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={qc}>
      <MemoryRouter><OrgMap orgId="org-1" /></MemoryRouter>
    </QueryClientProvider>,
  );
}

beforeEach(() => {
  sessionStorage.clear(); localStorage.clear();
  useAuthStore.setState({ apiKey: 'k', tenantId: 't', plan: 'free', isAuthenticated: true });
});
afterEach(() => vi.restoreAllMocks());

describe('OrgMap', () => {
  test('fetches departments and renders the toolbar once loaded', async () => {
    const spy = mockDepartments();
    renderMap();
    expect(await screen.findByLabelText('Search org map')).toBeInTheDocument();
    expect(screen.getByLabelText('Refresh org map')).toBeInTheDocument();
    expect(spy.mock.calls.some(([u]) => String(u).includes('/org/org-1/departments'))).toBe(true);
  });

  test('renders department nodes built from the fetched data', async () => {
    mockDepartments();
    renderMap();
    expect(await screen.findByText('Engineering')).toBeInTheDocument();
    expect(screen.getByText('Marketing')).toBeInTheDocument();
  });

  test('shows the loading spinner while departments are in flight', () => {
    vi.spyOn(globalThis, 'fetch').mockImplementation(() => new Promise(() => {}));
    renderMap();
    expect(screen.getByLabelText('Loading org map')).toBeInTheDocument();
  });

  test('typing in the search box updates its value', async () => {
    mockDepartments();
    renderMap();
    const input = await screen.findByLabelText<HTMLInputElement>('Search org map');
    await userEvent.type(input, 'Eng');
    expect(input.value).toBe('Eng');
  });

  test('clicking refresh re-requests the departments endpoint', async () => {
    const spy = mockDepartments();
    renderMap();
    await screen.findByLabelText('Search org map');
    const before = spy.mock.calls.filter(([u]) => String(u).includes('/departments')).length;
    await userEvent.click(screen.getByLabelText('Refresh org map'));
    await waitFor(() =>
      expect(spy.mock.calls.filter(([u]) => String(u).includes('/departments')).length).toBeGreaterThan(before),
    );
  });

  test('the clear-search button resets the search box and disappears', async () => {
    mockDepartments();
    renderMap();
    const input = await screen.findByLabelText<HTMLInputElement>('Search org map');
    await userEvent.type(input, 'Eng');
    const clearBtn = screen.getByLabelText('Clear search');
    await userEvent.click(clearBtn);
    expect(input.value).toBe('');
    expect(screen.queryByLabelText('Clear search')).not.toBeInTheDocument();
  });

  test('falls back to a default colour for a department name outside the known palette', async () => {
    mockDepartments([
      { id: 'd9', org_id: 'org-1', name: 'Zorptech Division', purpose: '', capability_domains: [], parent_dept_id: null, manager_agent_id: null, agent_count: 0, status: 'active', created_at: '', updated_at: '' },
    ]);
    renderMap();
    expect(await screen.findByText('Zorptech Division')).toBeInTheDocument();
  });

  test('defaults a department with no agent_count to 0 agents', async () => {
    mockDepartments([
      { id: 'd10', org_id: 'org-1', name: 'Ghost Team', purpose: '', capability_domains: [], parent_dept_id: null, manager_agent_id: null, agent_count: undefined as unknown as number, status: 'active', created_at: '', updated_at: '' },
    ]);
    renderMap();
    expect(await screen.findByText('Ghost Team')).toBeInTheDocument();
    expect(await screen.findByText('0 agents')).toBeInTheDocument();
  });

  test('unwraps a paginated { data: [...] } response shape for departments', async () => {
    vi.spyOn(globalThis, 'fetch').mockImplementation(async (input) => {
      const url = String(input);
      if (url.includes('/departments'))
        return new Response(JSON.stringify({ data: DEPARTMENTS }), { status: 200, headers: { 'Content-Type': 'application/json' } });
      return new Response('{}', { status: 200, headers: { 'Content-Type': 'application/json' } });
    });
    renderMap();
    expect(await screen.findByText('Engineering')).toBeInTheDocument();
  });
});
