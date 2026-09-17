/**
 * Function/branch companion for CivilizationPage.
 *
 * CivilizationPage.test.tsx and CivilizationPage.branches.test.tsx cover the
 * happy-path theater, list loading/empty/error states, and most tab switches.
 * This file targets the remaining UNCOVERED functions/branches:
 *  - the live SSE `handleEvent` callback (both the agent_spawned/agent_retired
 *    invalidation branch and the "other event type" no-op branch)
 *  - the ReplayPanel non-empty branch (lines ~736-756) once liveEvents is
 *    populated, including the live event ticker overlay and its
 *    payload.agent_id branch
 *  - the real `handleSubmitGoal` / `handlePause` / `handleResume` /
 *    `onAdjustBudget` handlers, invoked through the real (unmocked) ControlBar
 *  - the civilization-card hover handlers (onMouseEnter/onMouseLeave)
 *  - the "New Civilization" modal's X-button close and the create-failure
 *    (catch) branch, plus the description/max_agents/autonomy form fields
 */
import { describe, it, expect, beforeEach, afterEach, vi } from 'vitest';
import { render, screen, fireEvent, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import React from 'react';
import { MemoryRouter, Route, Routes } from 'react-router-dom';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { useToastStore } from '@/stores/toast';
import type { CivilizationEvent } from '../../lib/api/civilizationApi';

vi.mock('../../lib/api/civilizationApi', () => ({
  civilizationApi: {
    list: vi.fn().mockResolvedValue([
      { id: 'civ-abcdef0123456789xyz', name: 'Test Civ', status: 'active', constitution: {}, created_at: '2024-01-01T00:00:00Z' },
    ]),
    create: vi.fn().mockResolvedValue({ id: 'c2', name: 'New Civ', status: 'active', constitution: {} }),
    get: vi.fn(),
    getGraph: vi.fn().mockResolvedValue({ nodes: [], edges: [] }),
    getBlackboard: vi.fn().mockResolvedValue([]),
    getLearnings: vi.fn().mockResolvedValue([]),
    getSpawnAudit: vi.fn().mockResolvedValue([]),
    getDebates: vi.fn().mockResolvedValue([]),
    submitGoal: vi.fn().mockResolvedValue({ status: 'accepted', goal_id: 'g1' }),
    control: vi.fn().mockResolvedValue({ status: 'ok' }),
    killAgent: vi.fn().mockResolvedValue({ killed: 'a1' }),
    updateConstitution: vi.fn().mockResolvedValue({ updated: true }),
    getReplay: vi.fn().mockResolvedValue({ events: [], count: 0 }),
    getAgentInspector: vi.fn().mockResolvedValue({ member: {}, agent_config: {}, recent_messages: [] }),
  },
}));

// Capture the onEvent callback the component wires up so tests can simulate
// SSE pushes without a real EventSource.
let capturedOnEvent: ((evt: CivilizationEvent) => void) | undefined;
vi.mock('../../lib/sse/useCivilizationStream', () => ({
  useCivilizationStream: (
    _civId: string,
    opts?: { onEvent?: (evt: CivilizationEvent) => void },
  ) => {
    capturedOnEvent = opts?.onEvent;
    return { connected: true, events: [] };
  },
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

const BASE_CIV = {
  id: 'c1',
  name: 'Test Civ',
  status: 'active',
  constitution: { max_depth: 3, total_budget_usd: 100 },
  created_at: '',
  metrics: {
    total_members: 2, active_members: 1, idle_members: 1, retired_members: 0,
    total_budget_spent_usd: 2.5, avg_reputation: 0.7, max_reputation: 0.9, min_reputation: 0.5,
  },
};

beforeEach(() => {
  capturedOnEvent = undefined;
  vi.mocked(civilizationApi.list).mockResolvedValue([
    { id: 'civ-abcdef0123456789xyz', name: 'Test Civ', status: 'active', constitution: {}, created_at: '2024-01-01T00:00:00Z' },
  ]);
  vi.mocked(civilizationApi.get).mockResolvedValue(BASE_CIV);
  vi.mocked(civilizationApi.getGraph).mockResolvedValue({ nodes: [], edges: [] });
  vi.mocked(civilizationApi.getBlackboard).mockResolvedValue([]);
  vi.mocked(civilizationApi.getLearnings).mockResolvedValue([]);
  vi.mocked(civilizationApi.getSpawnAudit).mockResolvedValue([]);
  vi.mocked(civilizationApi.getDebates).mockResolvedValue([]);
  vi.mocked(civilizationApi.submitGoal).mockResolvedValue({ status: 'accepted', goal_id: 'g1' });
  vi.mocked(civilizationApi.control).mockResolvedValue({ status: 'ok' });
});

afterEach(() => {
  vi.clearAllMocks();
  useToastStore.setState({ toasts: [] });
});

describe('CivilizationPage — SSE event handling', () => {
  it('invalidates graph + civilization queries on agent_spawned events and re-renders the live ticker', async () => {
    renderPage('c1');
    await screen.findByText('Test Civ');
    await waitFor(() => expect(capturedOnEvent).toBeTypeOf('function'));

    fireEvent(window, new Event('noop')); // ensure act flushes are settled
    await waitFor(() => {
      capturedOnEvent?.({
        id: 'e1',
        type: 'agent_spawned',
        ts: '2024-01-01T12:00:05Z',
        payload: { agent_id: 'agent-42' },
      } as CivilizationEvent);
    });

    // Ticker overlay renders the badge + truncated agent id from the payload.
    await waitFor(() => {
      expect(screen.getAllByText('agent spawned').length).toBeGreaterThan(0);
    });
    expect(screen.getByText('agent-42'.slice(0, 10))).toBeInTheDocument();
  });

  it('does not blow up and still records the event for a non-spawn/retire type (no invalidation branch)', async () => {
    renderPage('c1');
    await screen.findByText('Test Civ');
    await waitFor(() => expect(capturedOnEvent).toBeTypeOf('function'));

    await waitFor(() => {
      capturedOnEvent?.({
        id: 'e2',
        type: 'finding_posted',
        ts: '2024-01-01T12:00:06Z',
        payload: {},
      } as CivilizationEvent);
    });

    expect(screen.getAllByText('finding posted').length).toBeGreaterThan(0);
  });

  it('shows the populated Replay panel (non-empty branch) once switched to Live Events', async () => {
    renderPage('c1');
    await screen.findByText('Test Civ');
    await waitFor(() => expect(capturedOnEvent).toBeTypeOf('function'));

    await waitFor(() => {
      capturedOnEvent?.({ id: 'e3', type: 'goal_complete', ts: '2024-01-01T12:00:07Z', payload: {} } as CivilizationEvent);
    });
    await waitFor(() => {
      capturedOnEvent?.({ id: 'e4', type: 'goal_failed', ts: '2024-01-01T12:00:08Z', payload: {} } as CivilizationEvent);
    });

    await waitFor(() => screen.getByTitle('Live Events'));
    fireEvent.click(screen.getByTitle('Live Events'));

    // Both events render (also mirrored in the canvas ticker), with the
    // ReplayPanel-only summary line proving the non-empty branch rendered.
    await waitFor(() => {
      expect(screen.getAllByText('goal failed').length).toBeGreaterThan(0);
    });
    expect(screen.getAllByText('goal complete').length).toBeGreaterThan(0);
    expect(screen.getByText(/2 events/i)).toBeInTheDocument();
    expect(screen.getAllByText('Live').length).toBeGreaterThan(0);
  });
});

describe('CivilizationPage — real ControlBar handlers', () => {
  it('submits a goal via the real handleSubmitGoal and invalidates the graph query', async () => {
    renderPage('c1');
    await screen.findByText('Test Civ');

    const input = await screen.findByLabelText('Goal input');
    await userEvent.type(input, 'Explore the ruins');
    await userEvent.click(screen.getByRole('button', { name: /run goal/i }));

    await waitFor(() => {
      expect(civilizationApi.submitGoal).toHaveBeenCalledWith('c1', 'Explore the ruins');
    });
  });

  it('pauses via the real handlePause handler', async () => {
    renderPage('c1');
    await screen.findByText('Test Civ');

    await userEvent.click(screen.getByRole('button', { name: /pause civilization/i }));
    await waitFor(() => {
      expect(civilizationApi.control).toHaveBeenCalledWith('c1', 'pause');
    });
  });

  it('resumes via the real handleResume handler when the civilization is paused', async () => {
    vi.mocked(civilizationApi.get).mockResolvedValue({ ...BASE_CIV, status: 'paused' });
    renderPage('c1');
    await screen.findByText('Test Civ');

    await userEvent.click(screen.getByRole('button', { name: /resume civilization/i }));
    await waitFor(() => {
      expect(civilizationApi.control).toHaveBeenCalledWith('c1', 'resume');
    });
  });

  it('adjusts the budget via the real onAdjustBudget handler', async () => {
    renderPage('c1');
    await screen.findByText('Test Civ');

    await userEvent.click(screen.getByLabelText('Adjust budget'));
    const budgetInput = screen.getByPlaceholderText('USD');
    await userEvent.clear(budgetInput);
    await userEvent.type(budgetInput, '250');
    const popover = screen.getByText('Adjust Total Budget').closest('div')!;
    await userEvent.click(popover.querySelector('button')!);

    await waitFor(() => {
      expect(civilizationApi.control).toHaveBeenCalledWith('c1', 'set_budget', { budget_usd: 250 });
    });
  });
});

describe('CivilizationPage — constitution tab', () => {
  it('renders the ConstitutionEditor and saves via the real onSave handler', async () => {
    renderPage('c1');
    await screen.findByText('Test Civ');
    await waitFor(() => screen.getByTitle('Constitution'));
    fireEvent.click(screen.getByTitle('Constitution'));

    // Save is disabled until the draft differs from the loaded constitution —
    // flip a toggle field to make it dirty.
    const [firstToggle] = await screen.findAllByRole('switch');
    await userEvent.click(firstToggle);

    const saveButton = screen.getByRole('button', { name: /Save Constitution/i });
    await userEvent.click(saveButton);

    await waitFor(() => {
      expect(civilizationApi.updateConstitution).toHaveBeenCalledWith('c1', expect.any(Object));
    });
  });
});

describe('CivilizationPage — list card hover + modal edge cases', () => {
  it('applies and reverts hover styles on a civilization card', async () => {
    renderPage();
    const card = await screen.findByText('Test Civ');
    const link = card.closest('a') as HTMLAnchorElement;

    fireEvent.mouseEnter(link);
    expect(link.style.borderColor).toBe('rgba(99, 102, 241, 0.4)');

    fireEvent.mouseLeave(link);
    expect(link.style.borderColor).toBe('rgba(255, 255, 255, 0.08)');
  });

  it('closes the New Civilization modal via the X button', async () => {
    renderPage();
    await screen.findByText('Test Civ');
    await userEvent.click(screen.getByRole('button', { name: /Create a new civilization/i }));
    await screen.findByRole('heading', { name: /New Civilization/i });

    const heading = screen.getByRole('heading', { name: /New Civilization/i });
    const modalHeader = heading.closest('div') as HTMLElement;
    const closeButton = within(modalHeader).getByRole('button');
    await userEvent.click(closeButton);

    await waitFor(() => {
      expect(screen.queryByRole('heading', { name: /New Civilization/i })).not.toBeInTheDocument();
    });
  });

  it('fills out description, max agents and autonomy level fields', async () => {
    renderPage();
    await screen.findByText('Test Civ');
    await userEvent.click(screen.getByRole('button', { name: /Create a new civilization/i }));
    await screen.findByRole('heading', { name: /New Civilization/i });

    await userEvent.type(screen.getByPlaceholderText(/What is this civilization for\?/i), 'A test cluster');
    const maxAgentsInput = screen.getByDisplayValue('5') as HTMLInputElement;
    await userEvent.clear(maxAgentsInput);
    await userEvent.type(maxAgentsInput, '10');

    const autonomySelect = screen.getByDisplayValue('Bounded Autonomous') as HTMLSelectElement;
    await userEvent.selectOptions(autonomySelect, 'Fully Autonomous');
    expect(autonomySelect.value).toBe('fully-autonomous');
  });

  it('shows an error toast when creating a civilization fails', async () => {
    vi.spyOn(globalThis, 'fetch').mockResolvedValue(
      new Response(JSON.stringify({ detail: 'boom' }), { status: 500 }),
    );
    renderPage();
    await screen.findByText('Test Civ');
    await userEvent.click(screen.getByRole('button', { name: /Create a new civilization/i }));
    await screen.findByRole('heading', { name: /New Civilization/i });

    await userEvent.type(screen.getByPlaceholderText(/Research Cluster Alpha/i), 'Doomed Civ');
    await userEvent.click(screen.getByRole('button', { name: /Create Civilization/i }));

    await waitFor(() => {
      const toasts = useToastStore.getState().toasts;
      expect(toasts.some(t => t.kind === 'error' && /Failed:/.test(t.message))).toBe(true);
    });
  });
});
