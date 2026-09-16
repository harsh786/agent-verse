/**
 * Branch companion for CivilizationPage.
 *
 * The existing CivilizationPage.test.tsx covers the happy-path theater and the
 * 503/500 list error states. This file covers the UNTESTED branches: the list
 * loading + empty states, the civilization cards, the "New Civilization" modal
 * (open / validation / create POST / cancel), the paused badge, the Overview
 * placeholder, and the remaining right-panel tabs (Members, Spawn Audit, Live
 * Events).
 *
 * The @xyflow/react + recharts stubs mirror the existing test so React Flow and
 * charts don't need real canvas/layout APIs.
 */
import { describe, it, expect, beforeEach, afterEach, vi } from 'vitest';
import { render, screen, fireEvent, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import React from 'react';
import { MemoryRouter, Route, Routes } from 'react-router-dom';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { useAuthStore } from '@/stores/auth';

vi.mock('../../lib/api/civilizationApi', () => ({
  civilizationApi: {
    list: vi.fn().mockResolvedValue([
      { id: 'civ-abcdef0123456789xyz', name: 'Test Civ', status: 'active', constitution: {}, created_at: '2024-01-01T00:00:00Z' },
    ]),
    create: vi.fn().mockResolvedValue({ id: 'c2', name: 'New Civ', status: 'active', constitution: {} }),
    get: vi.fn().mockResolvedValue({
      id: 'c1',
      name: 'Test Civ',
      status: 'active',
      constitution: { max_depth: 3 },
      created_at: '',
      metrics: {
        total_members: 2,
        active_members: 1,
        idle_members: 1,
        retired_members: 0,
        total_budget_spent_usd: 2.5,
        avg_reputation: 0.7,
        max_reputation: 0.9,
        min_reputation: 0.5,
      },
    }),
    getGraph: vi.fn().mockResolvedValue({ nodes: [], edges: [] }),
    getBlackboard: vi.fn().mockResolvedValue([]),
    getLearnings: vi.fn().mockResolvedValue([]),
    getSpawnAudit: vi.fn().mockResolvedValue([
      { id: 's1', requester_agent_id: 'agent-1', requested_capability: 'jira', decision: 'approved', reason: 'needed for triage', created_at: '2024-01-01T00:00:00Z' },
    ]),
    getDebates: vi.fn().mockResolvedValue([]),
    submitGoal: vi.fn().mockResolvedValue({ status: 'accepted', goal_id: 'g1' }),
    control: vi.fn().mockResolvedValue({ status: 'paused' }),
    killAgent: vi.fn().mockResolvedValue({ killed: 'a1' }),
    updateConstitution: vi.fn().mockResolvedValue({ updated: true }),
    getReplay: vi.fn().mockResolvedValue({ events: [], count: 0 }),
    getAgentInspector: vi.fn().mockResolvedValue({ member: {}, agent_config: {}, recent_messages: [] }),
  },
}));

vi.mock('../../lib/sse/useCivilizationStream', () => ({
  useCivilizationStream: () => ({ connected: true, events: [] }),
}));

vi.mock('@xyflow/react', () => ({
  ReactFlow: ({ children }: { children?: React.ReactNode }) => <div data-testid="react-flow">{children}</div>,
  ReactFlowProvider: ({ children }: { children?: React.ReactNode }) => <>{children}</>,
  Background: () => null,
  BackgroundVariant: { Dots: 'dots', Lines: 'lines', Cross: 'cross' },
  Controls: () => null,
  MiniMap: () => null,
  useNodesState: (nodes: unknown[]) => [nodes, vi.fn(), vi.fn()],
  useEdgesState: (edges: unknown[]) => [edges, vi.fn(), vi.fn()],
  useReactFlow: () => ({ fitView: vi.fn() }),
  Handle: () => null,
  Position: { Top: 'top', Bottom: 'bottom' },
}));

vi.mock('recharts', () => ({
  BarChart: () => null,
  Bar: () => null,
  Cell: () => null,
  XAxis: () => null,
  YAxis: () => null,
  Tooltip: () => null,
  ResponsiveContainer: ({ children }: { children?: React.ReactNode }) => <>{children}</>,
}));

import { CivilizationPage } from './CivilizationPage';
import { civilizationApi } from '../../lib/api/civilizationApi';

function renderPage(civId?: string) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={qc}>
      <MemoryRouter initialEntries={[civId ? `/civilization/${civId}` : '/civilization']}>
        <Routes>
          <Route path="/civilization/:id" element={<CivilizationPage />} />
          <Route path="/civilization" element={<CivilizationPage />} />
        </Routes>
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

const DEFAULT_CIV = {
  id: 'c1',
  name: 'Test Civ',
  status: 'active',
  constitution: { max_depth: 3 },
  created_at: '',
  metrics: {
    total_members: 2, active_members: 1, idle_members: 1, retired_members: 0,
    total_budget_spent_usd: 2.5, avg_reputation: 0.7, max_reputation: 0.9, min_reputation: 0.5,
  },
};

const DEFAULT_SPAWNS = [
  { id: 's1', requester_agent_id: 'agent-1', requested_capability: 'jira', decision: 'approved', reason: 'needed for triage', created_at: '2024-01-01T00:00:00Z' },
];

// vi.restoreAllMocks() (afterEach) resets the vi.fn() implementations on the
// mocked civilizationApi, so re-establish every default the tests rely on here.
// Per-test overrides (mockResolvedValueOnce / mockResolvedValue) still win.
beforeEach(() => {
  sessionStorage.clear();
  localStorage.clear();
  useAuthStore.setState({ apiKey: 'k', tenantId: 't', plan: 'free', isAuthenticated: true });
  vi.mocked(civilizationApi.list).mockResolvedValue([
    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    { id: 'civ-abcdef0123456789xyz', name: 'Test Civ', status: 'active', constitution: {}, created_at: '2024-01-01T00:00:00Z' } as any,
  ]);
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  vi.mocked(civilizationApi.get).mockResolvedValue(DEFAULT_CIV as any);
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  vi.mocked(civilizationApi.getGraph).mockResolvedValue({ nodes: [], edges: [] } as any);
  vi.mocked(civilizationApi.getBlackboard).mockResolvedValue([]);
  vi.mocked(civilizationApi.getLearnings).mockResolvedValue([]);
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  vi.mocked(civilizationApi.getSpawnAudit).mockResolvedValue(DEFAULT_SPAWNS as any);
  vi.mocked(civilizationApi.getDebates).mockResolvedValue([]);
});
afterEach(() => vi.restoreAllMocks());

describe('CivilizationPage — list branches', () => {
  it('shows the loading state while the civilization list is in flight', () => {
    vi.mocked(civilizationApi.list).mockReturnValueOnce(new Promise(() => {}) as Promise<never>);
    renderPage();
    expect(screen.getByText(/Loading civilizations…/i)).toBeInTheDocument();
  });

  it('shows the empty state when the tenant has no civilizations', async () => {
    vi.mocked(civilizationApi.list).mockResolvedValueOnce([]);
    renderPage();
    expect(await screen.findByText(/No civilizations yet/i)).toBeInTheDocument();
    expect(screen.getByText(/Create one via the API or backend/i)).toBeInTheDocument();
  });

  it('renders a civilization card with a link into the theater', async () => {
    renderPage();
    expect(await screen.findByText('Test Civ')).toBeInTheDocument();
    expect(screen.getByText(/Enter Theater/i)).toBeInTheDocument();
    // Card metric labels
    expect(screen.getByText('Rep')).toBeInTheDocument();
    expect(screen.getByText('Spent')).toBeInTheDocument();
  });
});

describe('CivilizationPage — new civilization modal', () => {
  it('opens the modal, keeps Create disabled until a name is typed, then POSTs', async () => {
    const fetchSpy = vi.spyOn(globalThis, 'fetch').mockResolvedValue(
      new Response(JSON.stringify({ id: 'new-1' }), { status: 200, headers: { 'Content-Type': 'application/json' } }),
    );
    renderPage();
    await screen.findByText('Test Civ');
    await userEvent.click(screen.getByRole('button', { name: /Create a new civilization/i }));

    expect(await screen.findByRole('heading', { name: /New Civilization/i })).toBeInTheDocument();
    const createBtn = screen.getByRole('button', { name: /Create Civilization/i });
    expect(createBtn).toBeDisabled();

    await userEvent.type(screen.getByPlaceholderText(/Research Cluster Alpha/i), 'Research Cluster');
    expect(createBtn).toBeEnabled();

    await userEvent.click(createBtn);
    await waitFor(() =>
      expect(
        fetchSpy.mock.calls.some(
          ([u, i]) => String(u).includes('/civilization/civilizations') && (i as RequestInit)?.method === 'POST',
        ),
      ).toBe(true),
    );
  });

  it('closes the modal via Cancel without creating anything', async () => {
    renderPage();
    await screen.findByText('Test Civ');
    await userEvent.click(screen.getByRole('button', { name: /Create a new civilization/i }));
    await screen.findByRole('heading', { name: /New Civilization/i });
    await userEvent.click(screen.getByRole('button', { name: /^Cancel$/i }));
    await waitFor(() =>
      expect(screen.queryByRole('heading', { name: /New Civilization/i })).not.toBeInTheDocument(),
    );
  });
});

describe('CivilizationPage — theater branches', () => {
  it('shows the PAUSED badge when the civilization is paused', async () => {
    vi.mocked(civilizationApi.get).mockResolvedValue({
      id: 'c1', name: 'Paused Civ', status: 'paused', constitution: {}, created_at: '',
      metrics: { total_members: 1, active_members: 0, idle_members: 1, retired_members: 0, total_budget_spent_usd: 0, avg_reputation: 0.5, max_reputation: 0.5, min_reputation: 0.5 },
      // eslint-disable-next-line @typescript-eslint/no-explicit-any
    } as any);
    renderPage('c1');
    expect(await screen.findByText('PAUSED')).toBeInTheDocument();
  });

  it('renders the Overview placeholder when the civilization has no metrics yet', async () => {
    vi.mocked(civilizationApi.get).mockResolvedValue({
      id: 'c1', name: 'Fresh Civ', status: 'active', constitution: {}, created_at: '',
      // eslint-disable-next-line @typescript-eslint/no-explicit-any
    } as any);
    renderPage('c1');
    expect(await screen.findByText(/Metrics will appear once agents are active/i)).toBeInTheDocument();
  });

  it('switching to the Spawn Audit tab shows the spawn timeline', async () => {
    renderPage('c1');
    await waitFor(() => screen.getByTitle('Spawn Audit'), { timeout: 3000 });
    fireEvent.click(screen.getByTitle('Spawn Audit'));
    expect(await screen.findByText('jira')).toBeInTheDocument();
    expect(screen.getByText(/1 approved/i)).toBeInTheDocument();
    expect(screen.getByText(/needed for triage/i)).toBeInTheDocument();
  });

  it('switching to the Live Events tab shows the empty replay placeholder', async () => {
    renderPage('c1');
    await waitFor(() => screen.getByTitle('Live Events'), { timeout: 3000 });
    fireEvent.click(screen.getByTitle('Live Events'));
    expect(await screen.findByText(/Live events will stream here during execution/i)).toBeInTheDocument();
  });

  it('activates the Members tab when selected', async () => {
    renderPage('c1');
    await waitFor(() => screen.getByTitle('Members'), { timeout: 3000 });
    const membersTab = screen.getByTitle('Members');
    fireEvent.click(membersTab);
    await waitFor(() => expect(membersTab).toHaveAttribute('aria-selected', 'true'));
  });

  it('renders live agent counts in the header from metrics', async () => {
    renderPage('c1');
    // metrics: 1 active / 2 total
    expect(await screen.findByText(/1 active/i)).toBeInTheDocument();
    const header = screen.getByText(/1 active/i).closest('div') as HTMLElement;
    expect(within(header).getByText(/2 total/i)).toBeInTheDocument();
  });
});
