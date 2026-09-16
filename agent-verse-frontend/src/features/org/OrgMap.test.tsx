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
  ReactFlow: ({ nodes }: { nodes?: Array<{ id: string; data?: { label?: unknown } }> }) => (
    <div data-testid="react-flow">
      {(nodes ?? []).map((n) => (
        <div key={n.id}>{String(n.data?.label ?? '')}</div>
      ))}
    </div>
  ),
  ReactFlowProvider: ({ children }: { children?: React.ReactNode }) => <>{children}</>,
  Background: () => null,
  Controls: () => null,
  MiniMap: () => null,
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
});
