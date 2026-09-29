import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { act, render, renderHook, screen, fireEvent, waitFor } from '@testing-library/react';
import React, { type ReactNode } from 'react';
import { afterEach, beforeEach, describe, expect, test, vi } from 'vitest';
import { useAuthStore } from '@/stores/auth';
import { DigitalTwinPanel, useWhatIf, type WhatIfOutcome } from './DigitalTwinPanel';

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

const CAPACITY = {
  org_id: 'o1',
  current_utilisation: { Finance: 0.92, Engineering: 0.5 },
  queued_missions: 7,
  estimated_clear_h: 3.5,
  underutilised_depts: ['Design'],
  overloaded_depts: ['Finance'],
  active_teams: 2,
  pending_approvals: 1,
  recommendations: ['Rebalance load off Finance', 'Spin up a second reviewer'],
};

const SIM = {
  mission_title: 'Reconcile books',
  estimated_duration_h: 4,
  estimated_cost_usd: 12.5,
  resource_usage: {},
  bottlenecks: [],
  recommendations: ['Add an accountant agent'],
  feasible: true,
  confidence: 0.82,
};

// What the backend now returns when it has no real data to measure from.
const CAPACITY_UNMEASURED = {
  org_id: 'o1',
  current_utilisation: { Engineering: 0.5, Legal: null },
  departments: [
    { dept_id: 'd-eng', name: 'Engineering', agent_count: 2, busy_agent_count: 1,
      active_task_count: 1, queued_task_count: 0, utilisation: 0.5, reason: null },
    { dept_id: 'd-legal', name: 'Legal', agent_count: 0, busy_agent_count: 0,
      active_task_count: 0, queued_task_count: 0, utilisation: null,
      reason: "No agents are staffed on this department's active teams." },
  ],
  queued_missions: 0,
  estimated_clear_h: null,
  estimated_clear_reason: 'Time-to-clear is not estimated.',
  underutilised_depts: [],
  overloaded_depts: [],
  active_teams: 1,
  pending_approvals: 0,
  recommendations: [],
};

const SIM_NO_HISTORY = {
  mission_title: 'Reconcile books',
  estimated_duration_h: null,
  estimated_cost_usd: null,
  resource_usage: { staffed_agents: 0, busy_agents: 0, available_agents: 0 },
  bottlenecks: ['No agents are staffed on any active team.'],
  recommendations: [],
  feasible: false,
  confidence: null,
  sample_size: 0,
  estimate_reason: "No completed 'medium'-priority missions yet.",
};

function mockFetch(capacity: unknown = CAPACITY, sim: unknown = SIM) {
  return vi.spyOn(globalThis, 'fetch').mockImplementation(async (input, init) => {
    const url = String(input);
    const method = (init?.method ?? 'GET').toUpperCase();
    if (url.includes('/twin/what-if') && method === 'POST')
      return new Response(
        JSON.stringify({ detail: { status: 501, detail: 'What-if re-simulation is not implemented.' } }),
        { status: 501, headers: { 'Content-Type': 'application/json' } },
      );
    if (url.includes('/twin/simulate') && method === 'POST')
      return new Response(JSON.stringify(sim), { status: 200, headers: { 'Content-Type': 'application/json' } });
    if (url.includes('/twin/capacity'))
      return new Response(JSON.stringify(capacity), { status: 200, headers: { 'Content-Type': 'application/json' } });
    return new Response('{}', { status: 200, headers: { 'Content-Type': 'application/json' } });
  });
}

function renderPanel() {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={qc}>
      <DigitalTwinPanel orgId="o1" />
    </QueryClientProvider>,
  );
}

beforeEach(() => {
  sessionStorage.clear(); localStorage.clear();
  useAuthStore.setState({ apiKey: 'k', tenantId: 't', plan: 'free', isAuthenticated: true });
});
afterEach(() => vi.restoreAllMocks());

describe('DigitalTwinPanel', () => {
  test('renders the header and summary stats from the capacity endpoint', async () => {
    mockFetch();
    renderPanel();
    expect(screen.getByText('Digital Twin')).toBeInTheDocument();
    expect(screen.getByText('READ-ONLY')).toBeInTheDocument();
    // Queued missions count.
    expect(await screen.findByText('7')).toBeInTheDocument();
    expect(screen.getByText('Queued')).toBeInTheDocument();
    expect(screen.getByText('3.5h')).toBeInTheDocument();
  });

  test('renders the overloaded-department alert', async () => {
    mockFetch();
    renderPanel();
    expect(await screen.findByText('Overloaded:')).toBeInTheDocument();
    // "Finance" appears in both the alert and the utilisation gauge.
    expect(screen.getAllByText('Finance').length).toBeGreaterThanOrEqual(1);
  });

  test('renders a utilisation meter per department with the correct percent', async () => {
    mockFetch();
    renderPanel();
    const meters = await screen.findAllByRole('meter');
    expect(meters.length).toBe(2);
    expect(screen.getByLabelText(/Finance utilisation: 92%/i)).toBeInTheDocument();
    expect(screen.getByLabelText(/Engineering utilisation: 50%/i)).toBeInTheDocument();
  });

  test('renders recommendations', async () => {
    mockFetch();
    renderPanel();
    expect(await screen.findByText('Rebalance load off Finance')).toBeInTheDocument();
    expect(screen.getByText('Spin up a second reviewer')).toBeInTheDocument();
  });

  test('shows the empty state when there is no capacity data', async () => {
    mockFetch(null);
    renderPanel();
    expect(await screen.findByText('No capacity data available.')).toBeInTheDocument();
  });

  test('the simulator posts to /twin/simulate and renders the result', async () => {
    const spy = mockFetch();
    renderPanel();
    await screen.findByText('Digital Twin');
    fireEvent.click(screen.getByRole('button', { name: /Simulate a mission/i }));
    fireEvent.change(screen.getByLabelText(/Mission title to simulate/i), { target: { value: 'Reconcile books' } });
    fireEvent.click(screen.getByRole('button', { name: /Run simulation for this mission/i }));

    await waitFor(() =>
      expect(
        spy.mock.calls.some(([u, i]) => String(u).includes('/twin/simulate') && (i as RequestInit)?.method === 'POST'),
      ).toBe(true),
    );
    // Result tiles surface the simulated numbers.
    expect(await screen.findByText('4h')).toBeInTheDocument();
    expect(screen.getByText('$12.5')).toBeInTheDocument();
    expect(screen.getByText('82%')).toBeInTheDocument();
    expect(screen.getByText('Add an accountant agent')).toBeInTheDocument();
  });

  test('a department with null utilisation shows "Not available" + reason, not a meter', async () => {
    mockFetch(CAPACITY_UNMEASURED);
    renderPanel();
    expect(await screen.findByLabelText(/Legal utilisation: not available/i)).toBeInTheDocument();
    expect(screen.getByText("No agents are staffed on this department's active teams.")).toBeInTheDocument();
    // Only the measured department gets a meter.
    const meters = screen.getAllByRole('meter');
    expect(meters).toHaveLength(1);
    expect(screen.getByLabelText(/Engineering utilisation: 50%/i)).toBeInTheDocument();
    expect(screen.queryByLabelText(/Legal utilisation: \d+%/i)).not.toBeInTheDocument();
  });

  test('a null time-to-clear renders n/a instead of a number', async () => {
    mockFetch(CAPACITY_UNMEASURED);
    renderPanel();
    expect(await screen.findByText('n/a')).toBeInTheDocument();
    expect(screen.getByText('n/a').closest('[title]')).toHaveAttribute('title', 'Time-to-clear is not estimated.');
    expect(screen.queryByText(/NaN|null/)).not.toBeInTheDocument();
  });

  test('a simulation without history shows "Not available", the reason and the sample size', async () => {
    mockFetch(CAPACITY, SIM_NO_HISTORY);
    renderPanel();
    await screen.findByText('Digital Twin');
    fireEvent.click(screen.getByRole('button', { name: /Simulate a mission/i }));
    fireEvent.change(screen.getByLabelText(/Mission title to simulate/i), { target: { value: 'Reconcile books' } });
    fireEvent.click(screen.getByRole('button', { name: /Run simulation for this mission/i }));

    expect(await screen.findByTestId('sim-estimate-reason')).toHaveTextContent(
      "No completed 'medium'-priority missions yet.",
    );
    expect(screen.getAllByText('Not available')).toHaveLength(2);
    expect(screen.getByText('0 missions')).toBeInTheDocument();
    expect(screen.getByText('Based on')).toBeInTheDocument();
    expect(screen.queryByText('Confidence')).not.toBeInTheDocument();
    expect(screen.getByText('No agents are staffed on any active team.')).toBeInTheDocument();
    expect(screen.queryByText(/NaN|\$null|nullh/)).not.toBeInTheDocument();
  });
});

describe('useWhatIf', () => {
  test('maps the backend 501 to { available: false } instead of a projection', async () => {
    mockFetch();
    const qc = new QueryClient({ defaultOptions: { mutations: { retry: false } } });
    const wrapper = ({ children }: { children: ReactNode }) => (
      <QueryClientProvider client={qc}>{children}</QueryClientProvider>
    );
    const { result } = renderHook(() => useWhatIf('o1'), { wrapper });
    let outcome: WhatIfOutcome | undefined;
    await act(async () => {
      outcome = await result.current.mutateAsync({ add_agents: 3 });
    });
    expect(outcome).toEqual({ available: false, reason: 'What-if re-simulation is not implemented.' });
  });
});
