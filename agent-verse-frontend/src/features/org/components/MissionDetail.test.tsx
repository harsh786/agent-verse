import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { render, screen, fireEvent, waitFor } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import React, { type ReactNode } from 'react';
import { afterEach, beforeEach, describe, expect, test, vi } from 'vitest';
import { useAuthStore } from '@/stores/auth';
import { MissionDetail } from './MissionDetail';

vi.mock('framer-motion', async (importOriginal) => {
  const actual = await importOriginal<typeof import('framer-motion')>();
  const stubCache = new Map<string, (props: { children?: ReactNode; [k: string]: unknown }) => React.ReactElement>();
  const makeStub = (tag: string) => {
    let stub = stubCache.get(tag);
    if (!stub) {
      stub = ({ children, ...props }) => React.createElement(tag, props as Record<string, unknown>, children);
      stubCache.set(tag, stub);
    }
    return stub;
  };
  return {
    ...actual,
    useReducedMotion: () => true,
    AnimatePresence: ({ children }: { children?: ReactNode }) => <>{children}</>,
    motion: new Proxy(actual.motion as unknown as Record<string, unknown>, {
      get: (target, key: string) => (key in target ? target[key] : makeStub(key)),
    }),
  };
});

const ORG_ID = 'o1';
const MISSION_ID = 'm1';

function makeMission(over: Record<string, unknown> = {}) {
  return {
    id: MISSION_ID,
    tenant_id: 't',
    org_id: ORG_ID,
    dept_id: null,
    assigned_team_id: null,
    title: 'Ship the launch',
    objective: 'Coordinate the v2 launch across teams',
    why: '',
    expected_outcome: '',
    status: 'draft',
    priority: 'high',
    source: 'user',
    autonomy_level: null,
    budget_usd: null,
    deadline: null,
    tags: [],
    created_by: null,
    outputs: [],
    evidence: [],
    started_at: null,
    completed_at: null,
    created_at: '2026-09-16T00:00:00Z',
    updated_at: '2026-09-16T00:00:00Z',
    metadata: {},
    published: null,
    publish_pending: false,
    ...over,
  };
}

const EVENTS = [
  { id: 'e1', org_id: ORG_ID, event_type: 'mission', title: 'Team formed', description: '', severity: 'info', entity_type: 'mission', entity_id: MISSION_ID, created_at: '2026-09-16T09:00:00Z' },
];

const TIMELINE = { mission_id: MISSION_ID, status: 'active', phases: [], total_ms: null };

const GOAL_STATE = {
  goal_id: 'g1',
  status: 'executing',
  plan: ['Gather data', 'Draft plan'],
  steps: [{ step: 'Gather data', status: 'complete' }],
  iterations: 2,
};

interface FetchOpts {
  mission?: Record<string, unknown> | null;
  events?: unknown[];
  goal?: unknown;
  /** Org approval-gate list (GET /v1/org/o1/approvals). */
  orgApprovals?: unknown[];
  /** Governance HITL list (GET /governance/approvals). */
  hitl?: unknown[];
  /** Status for the decision POSTs (approvals/.../approve|reject). */
  decisionStatus?: number;
  decisionBody?: unknown;
}

const json = (body: unknown, status = 200) =>
  new Response(JSON.stringify(body), { status, headers: { 'Content-Type': 'application/json' } });

function mockFetch(opts: FetchOpts = {}) {
  const mission = opts.mission === undefined ? makeMission() : opts.mission;
  const events = opts.events ?? EVENTS;
  return vi.spyOn(globalThis, 'fetch').mockImplementation(async (input, init) => {
    const url = String(input);
    const method = (init?.method ?? 'GET').toUpperCase();
    if (/\/missions\/m1\/status$/.test(url) && method === 'POST')
      return new Response(JSON.stringify(makeMission({ status: 'active' })), { status: 200, headers: { 'Content-Type': 'application/json' } });
    if (/\/approvals\/[^/]+\/(approve|reject)$/.test(url) && method === 'POST')
      return json(opts.decisionBody ?? { status: 'approved' }, opts.decisionStatus ?? 200);
    if (/\/v1\/org\/o1\/approvals$/.test(url))
      return json({ data: opts.orgApprovals ?? [], org_id: ORG_ID, total: (opts.orgApprovals ?? []).length });
    if (/\/governance\/approvals$/.test(url))
      return json(opts.hitl ?? []);
    if (/\/missions\/m1\/timeline$/.test(url))
      return new Response(JSON.stringify(TIMELINE), { status: 200, headers: { 'Content-Type': 'application/json' } });
    if (/\/missions\/m1$/.test(url))
      return new Response(JSON.stringify(mission), { status: 200, headers: { 'Content-Type': 'application/json' } });
    if (/\/goals\/g1$/.test(url))
      return new Response(JSON.stringify(opts.goal ?? GOAL_STATE), { status: 200, headers: { 'Content-Type': 'application/json' } });
    if (url.includes('/events'))
      return new Response(JSON.stringify(events), { status: 200, headers: { 'Content-Type': 'application/json' } });
    return new Response('{}', { status: 200, headers: { 'Content-Type': 'application/json' } });
  });
}

function renderDetail(onClose = vi.fn()) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  const utils = render(
    <QueryClientProvider client={qc}>
      <MemoryRouter>
        <MissionDetail orgId={ORG_ID} missionId={MISSION_ID} onClose={onClose} />
      </MemoryRouter>
    </QueryClientProvider>,
  );
  return { ...utils, onClose };
}

beforeEach(() => {
  sessionStorage.clear(); localStorage.clear();
  useAuthStore.setState({ apiKey: 'k', tenantId: 't', plan: 'free', isAuthenticated: true });
});
afterEach(() => vi.restoreAllMocks());

describe('MissionDetail', () => {
  test('renders the mission header, objective, status and priority', async () => {
    mockFetch();
    renderDetail();
    expect(await screen.findByRole('heading', { name: 'Ship the launch' })).toBeInTheDocument();
    expect(screen.getByText('Coordinate the v2 launch across teams')).toBeInTheDocument();
    // status pill + priority (draft mission, high priority).
    expect(screen.getByText('draft')).toBeInTheDocument();
    expect(screen.getByText('high')).toBeInTheDocument();
  });

  test('renders the Timeline ribbon from the mission timeline endpoint', async () => {
    const spy = mockFetch();
    renderDetail();
    await screen.findByRole('heading', { name: 'Ship the launch' });
    expect(await screen.findByText('Timeline')).toBeInTheDocument();
    // "No timeline yet" for an empty phase list.
    expect(await screen.findByText('No timeline yet')).toBeInTheDocument();
    await waitFor(() =>
      expect(spy.mock.calls.some(([u]) => /\/missions\/m1\/timeline$/.test(String(u)))).toBe(true),
    );
  });

  test('renders the orchestration plan (Agent Team Formed) from metadata', async () => {
    mockFetch({
      mission: makeMission({
        metadata: {
          orchestration_plan_summary: {
            topology: 'hierarchical',
            departments: ['finance', 'engineering'],
            autonomy_level: 3,
            estimated_cost_usd: 0.42,
          },
        },
      }),
    });
    renderDetail();
    expect(await screen.findByText('Agent Team Formed')).toBeInTheDocument();
    expect(screen.getByText('hierarchical')).toBeInTheDocument();
    expect(screen.getByText('finance')).toBeInTheDocument();
    expect(screen.getByText('engineering')).toBeInTheDocument();
    expect(screen.getByText(/Est\. cost: \$0\.420/)).toBeInTheDocument();
  });

  test('renders the live agent execution plan when a goal_id is present', async () => {
    mockFetch({ mission: makeMission({ metadata: { goal_id: 'g1' } }) });
    renderDetail();
    await screen.findByRole('heading', { name: 'Ship the launch' });
    expect(await screen.findByText('Agent Execution')).toBeInTheDocument();
    expect(await screen.findByText(/Execution Plan — 2 steps/i)).toBeInTheDocument();
    expect(screen.getByText('Gather data')).toBeInTheDocument();
    expect(screen.getByText('Draft plan')).toBeInTheDocument();
  });

  test('renders the live activity feed, and an empty state when there are none', async () => {
    mockFetch();
    const { unmount } = renderDetail();
    expect(await screen.findByText('Team formed')).toBeInTheDocument();
    unmount();

    vi.restoreAllMocks();
    mockFetch({ events: [] });
    renderDetail();
    expect(await screen.findByText('No activity yet…')).toBeInTheDocument();
  });

  test('activating a draft mission POSTs the new status', async () => {
    const spy = mockFetch();
    renderDetail();
    await screen.findByRole('heading', { name: 'Ship the launch' });
    fireEvent.click(screen.getByRole('button', { name: 'Activate' }));
    await waitFor(() =>
      expect(
        spy.mock.calls.some(([u, i]) => /\/missions\/m1\/status$/.test(String(u)) && (i as RequestInit)?.method === 'POST'),
      ).toBe(true),
    );
  });

  const GATE = {
    id: 'gate1', task_id: 'gate1', mission_id: MISSION_ID, status: 'pending',
    title: 'Approval gate: deploy', action: 'deploy',
  };
  const OTHER_GATE = { ...GATE, id: 'gate9', task_id: 'gate9', mission_id: 'm9', title: 'Approval gate: other' };

  const decisionCalls = (spy: ReturnType<typeof mockFetch>) =>
    spy.mock.calls
      .filter(([, i]) => ((i as RequestInit | undefined)?.method ?? 'GET').toUpperCase() === 'POST')
      .map(([u]) => String(u));

  test("a gated mission's Approve posts the approval-gate id, never the mission id", async () => {
    const spy = mockFetch({
      mission: makeMission({ status: 'review' }),
      orgApprovals: [OTHER_GATE, GATE],
    });
    renderDetail();
    expect(await screen.findByText('Human Approval Required')).toBeInTheDocument();
    expect(screen.getByText('Approval gate: deploy')).toBeInTheDocument();
    // Another mission's gate is not offered here.
    expect(screen.queryByText('Approval gate: other')).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: /Approve/i }));
    await waitFor(() =>
      expect(decisionCalls(spy).some((u) => /\/v1\/org\/o1\/approvals\/gate1\/approve$/.test(u))).toBe(true),
    );
    expect(decisionCalls(spy).some((u) => u.includes('/tasks/m1/'))).toBe(false);
    // Approving does not flip the mission status client-side: the server dispatches.
    expect(decisionCalls(spy).some((u) => /\/missions\/m1\/status$/.test(u))).toBe(false);
  });

  test('a refused approval (403) renders an alert instead of failing silently', async () => {
    mockFetch({
      mission: makeMission({ status: 'review' }),
      orgApprovals: [GATE],
      decisionStatus: 403,
      decisionBody: { detail: 'Requires org role team_lead' },
    });
    renderDetail();
    await screen.findByText('Approval gate: deploy');
    fireEvent.click(screen.getByRole('button', { name: /Approve/i }));
    expect(await screen.findByRole('alert')).toHaveTextContent(/team_lead/);
  });

  test('Reject posts the gate id and a 404 is shown, not swallowed', async () => {
    const spy = mockFetch({
      mission: makeMission({ status: 'review' }),
      orgApprovals: [GATE],
      decisionStatus: 404,
      decisionBody: { detail: 'Approval gate1 not found' },
    });
    renderDetail();
    await screen.findByText('Approval gate: deploy');
    fireEvent.click(screen.getByRole('button', { name: /Reject/i }));
    expect(await screen.findByRole('alert')).toHaveTextContent(/not found/);
    expect(decisionCalls(spy).some((u) => /\/v1\/org\/o1\/approvals\/gate1\/reject$/.test(u))).toBe(true);
  });

  test("a goal waiting on a human resolves that goal's HITL request", async () => {
    const spy = mockFetch({
      mission: makeMission({ status: 'active', metadata: { goal_id: 'g1' } }),
      goal: { goal_id: 'g1', status: 'waiting_human', plan: [], steps: [] },
      hitl: [
        { request_id: 'r-other', goal_id: 'g-other', action: 'other', status: 'pending' },
        { request_id: 'r1', goal_id: 'g1', action: 'deploy to prod', status: 'pending' },
      ],
    });
    renderDetail();
    expect(await screen.findByText('Human Approval Required')).toBeInTheDocument();
    expect(await screen.findByText('deploy to prod')).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: /Approve/i }));
    await waitFor(() =>
      expect(decisionCalls(spy).some((u) => /\/governance\/approvals\/r1\/approve$/.test(u))).toBe(true),
    );
    expect(decisionCalls(spy).some((u) => u.includes('/tasks/m1/'))).toBe(false);
  });

  test('a failing approvals list is reported, not shown as nothing to approve', async () => {
    vi.spyOn(globalThis, 'fetch').mockImplementation(async (input) => {
      const url = String(input);
      if (/\/v1\/org\/o1\/approvals$/.test(url)) return json({ detail: 'boom' }, 500);
      if (/\/missions\/m1$/.test(url)) return json(makeMission({ status: 'review' }));
      if (/\/missions\/m1\/timeline$/.test(url)) return json(TIMELINE);
      return json([]);
    });
    renderDetail();
    expect(await screen.findByText(/Could not load pending approvals/i)).toBeInTheDocument();
  });

  test('close button and Escape key both invoke onClose; missing mission shows not-found', async () => {
    // Close button + Escape.
    mockFetch();
    const { onClose, unmount } = renderDetail();
    await screen.findByRole('heading', { name: 'Ship the launch' });
    fireEvent.click(screen.getByRole('button', { name: /Close mission detail/i }));
    expect(onClose).toHaveBeenCalledTimes(1);
    fireEvent.keyDown(document, { key: 'Escape' });
    expect(onClose).toHaveBeenCalledTimes(2);
    unmount();

    // Not-found state.
    vi.restoreAllMocks();
    mockFetch({ mission: null });
    renderDetail();
    expect(await screen.findByText('Mission not found')).toBeInTheDocument();
  });
});
