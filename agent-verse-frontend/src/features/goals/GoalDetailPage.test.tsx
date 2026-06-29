import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter, Route, Routes } from 'react-router-dom';
import { afterEach, beforeEach, describe, expect, test, vi } from 'vitest';
import { GoalDetailPage } from './GoalDetailPage';
import * as goalStreamModule from '@/lib/sse/useGoalStream';

// Mock SSE hook – always returns a fixed set of events
vi.mock('@/lib/sse/useGoalStream', () => ({
  useGoalStream: () => ({
    connected: true,
    streamingToken: null,
    events: [
      { type: 'goal_started', status: 'executing' },
      { type: 'plan_ready', steps: ['Gather context', 'Execute plan'] },
      {
        type: 'tool_call_complete',
        tool: 'jira.search',
        success: true,
        server_id: 'jira',
        output: { issues: 2 },
      },
      {
        type: 'tool_call_failed',
        tool: 'github.create_pr',
        server_id: 'github',
        error: 'Token expired',
      },
      { type: 'verification_done', success: false, reason: 'Tests failed' },
    ],
  }),
}));

function renderGoalDetailPage() {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  render(
    <QueryClientProvider client={queryClient}>
      <MemoryRouter initialEntries={['/goals/goal-1']}>
        <Routes>
          <Route path="/goals/:goalId" element={<GoalDetailPage />} />
        </Routes>
      </MemoryRouter>
    </QueryClientProvider>
  );
}

/** Default mock for non-HITL states (no approvals query fires) */
function mockGoal(status: string, goal = 'Fix prod') {
  return vi.spyOn(globalThis, 'fetch').mockImplementation(async (input, init) => {
    const url = String(input);
    if (url.includes('/cancel') && init?.method === 'POST') {
      return new Response(
        JSON.stringify({ id: 'goal-1', goal_id: 'goal-1', status: 'cancelled', goal }),
        { status: 200, headers: { 'Content-Type': 'application/json' } }
      );
    }
    // Default: return goal data
    return new Response(
      JSON.stringify({ id: 'goal-1', goal_id: 'goal-1', status, goal }),
      { status: 200, headers: { 'Content-Type': 'application/json' } }
    );
  });
}

/** Mock for waiting_human with a matching pending approval */
function mockWaitingHumanGoal() {
  return vi.spyOn(globalThis, 'fetch').mockImplementation(async (input) => {
    const url = String(input);
    if (url.endsWith('/governance/approvals')) {
      return new Response(
        JSON.stringify([
          { request_id: 'req-1', goal_id: 'goal-1', status: 'pending', tool_name: 'shell:execute' },
        ]),
        { status: 200, headers: { 'Content-Type': 'application/json' } }
      );
    }
    if (url.includes('/approve') || url.includes('/reject')) {
      return new Response(
        JSON.stringify({ status: 'approved' }),
        { status: 200, headers: { 'Content-Type': 'application/json' } }
      );
    }
    return new Response(
      JSON.stringify({ id: 'goal-1', goal_id: 'goal-1', status: 'waiting_human', goal: 'Fix prod' }),
      { status: 200, headers: { 'Content-Type': 'application/json' } }
    );
  });
}

describe('GoalDetailPage', () => {
  beforeEach(() => {
    localStorage.clear();
    localStorage.setItem('av_api_key', 'tenant-key');
  });

  afterEach(() => {
    vi.restoreAllMocks();
  });

  test('renders typed execution events with meaningful labels and details', async () => {
    mockGoal('executing');

    renderGoalDetailPage();

    expect(await screen.findByText('Goal started')).toBeInTheDocument();
    expect(screen.getByText('Plan ready')).toBeInTheDocument();
    expect(screen.getByText('jira.search succeeded')).toBeInTheDocument();
    expect(screen.getByText('github.create_pr failed')).toBeInTheDocument();
    expect(screen.getByText('Verification failed')).toBeInTheDocument();

    await userEvent.click(screen.getByText('Plan ready'));
    expect(screen.getByText(/Gather context/)).toBeInTheDocument();
    expect(screen.getByText(/Execute plan/)).toBeInTheDocument();

    await userEvent.click(screen.getByText('jira.search succeeded'));
    expect(screen.getByText(/Server: jira/)).toBeInTheDocument();
    expect(screen.getByText(/"issues": 2/)).toBeInTheDocument();

    await userEvent.click(screen.getByText('github.create_pr failed'));
    expect(screen.getByText(/Server: github/)).toBeInTheDocument();
    expect(screen.getByText(/Token expired/)).toBeInTheDocument();
  });

  test('shows goal text and status badge in header', async () => {
    mockGoal('executing', 'Fix production bug');
    renderGoalDetailPage();
    expect(await screen.findByText('Fix production bug')).toBeInTheDocument();
    expect(screen.getByText('executing')).toBeInTheDocument();
  });

  test('shows HITL panel with Approve/Reject when matching approval exists', async () => {
    mockWaitingHumanGoal();
    renderGoalDetailPage();
    await waitFor(() => {
      expect(screen.getByText('Human approval required')).toBeInTheDocument();
      expect(screen.getByText('Approve')).toBeInTheDocument();
      expect(screen.getByText('Reject')).toBeInTheDocument();
    });
  });

  test('Approve button calls the governance approve API', async () => {
    const fetchMock = mockWaitingHumanGoal();
    renderGoalDetailPage();
    await waitFor(() => expect(screen.getByText('Approve')).toBeInTheDocument());
    await userEvent.click(screen.getByText('Approve'));

    await waitFor(() =>
      expect(fetchMock).toHaveBeenCalledWith(
        expect.stringMatching(/\/governance\/approvals\/req-1\/approve$/),
        expect.objectContaining({ method: 'POST' })
      )
    );
  });

  test('Reject button calls the governance reject API', async () => {
    const fetchMock = mockWaitingHumanGoal();
    renderGoalDetailPage();
    await waitFor(() => expect(screen.getByText('Reject')).toBeInTheDocument());
    await userEvent.click(screen.getByText('Reject'));

    await waitFor(() =>
      expect(fetchMock).toHaveBeenCalledWith(
        expect.stringMatching(/\/governance\/approvals\/req-1\/reject$/),
        expect.objectContaining({ method: 'POST' })
      )
    );
  });

  test('does not show HITL panel when goal is executing', async () => {
    mockGoal('executing');
    renderGoalDetailPage();
    await waitFor(() => expect(screen.getByText('Goal started')).toBeInTheDocument());
    expect(screen.queryByText('Human approval required')).not.toBeInTheDocument();
  });

  test('shows cancel button for goals in executing status', async () => {
    mockGoal('executing');
    renderGoalDetailPage();
    expect(await screen.findByRole('button', { name: /cancel/i })).toBeInTheDocument();
  });

  test('shows cancel button for goals in planning status', async () => {
    mockGoal('planning');
    renderGoalDetailPage();
    expect(await screen.findByRole('button', { name: /cancel/i })).toBeInTheDocument();
  });

  test('does not show cancel button when goal is complete', async () => {
    mockGoal('complete');
    renderGoalDetailPage();
    await waitFor(() => expect(screen.getByText('Fix prod')).toBeInTheDocument());
    expect(screen.queryByRole('button', { name: /^cancel$/i })).not.toBeInTheDocument();
  });

  test('cancel button calls the cancel API endpoint', async () => {
    const fetchMock = mockGoal('executing');

    renderGoalDetailPage();
    const cancelBtn = await screen.findByRole('button', { name: /cancel/i });
    await userEvent.click(cancelBtn);

    await waitFor(() =>
      expect(fetchMock).toHaveBeenCalledWith(
        expect.stringMatching(/\/goals\/goal-1\/cancel$/),
        expect.objectContaining({ method: 'POST' })
      )
    );
  });

  test('shows "Goal not found" when fetch returns unknown goal', async () => {
    vi.spyOn(globalThis, 'fetch').mockResolvedValue(
      new Response(JSON.stringify(null), {
        status: 200,
        headers: { 'Content-Type': 'application/json' },
      })
    );
    renderGoalDetailPage();
    await waitFor(() => expect(screen.getByText(/goal not found/i)).toBeInTheDocument());
  });
});

// ── Token streaming display tests ─────────────────────────────────────────────

describe('GoalDetailPage — token streaming display', () => {
  beforeEach(() => {
    localStorage.clear();
    localStorage.setItem('av_api_key', 'tenant-key');
  });

  afterEach(() => {
    vi.restoreAllMocks();
  });

  test('shows live token stream panel when streamingToken is active', async () => {
    mockGoal('executing');

    // Override the mock for this test to return an active streamingToken
    vi.spyOn(goalStreamModule, 'useGoalStream').mockReturnValue({
      connected: true,
      streamingToken: {
        step: 'Analyse the codebase',
        cumulative: 'I will start by looking at',
      },
      events: [],
    });

    renderGoalDetailPage();

    await waitFor(() => {
      expect(screen.getByRole('status', { name: /live llm output/i })).toBeInTheDocument();
    });
    expect(screen.getByText(/Generating: Analyse the codebase/i)).toBeInTheDocument();
    expect(screen.getByText(/I will start by looking at/)).toBeInTheDocument();
  });

  test('does not show streaming panel when streamingToken is null', async () => {
    mockGoal('executing');

    vi.spyOn(goalStreamModule, 'useGoalStream').mockReturnValue({
      connected: true,
      streamingToken: null,
      events: [],
    });

    renderGoalDetailPage();

    await waitFor(() => expect(screen.queryByRole('button', { name: /cancel/i })).toBeInTheDocument());
    expect(screen.queryByRole('status', { name: /live llm output/i })).not.toBeInTheDocument();
  });

  test('streaming panel disappears when streamingToken is null (cleared state)', async () => {
    mockGoal('executing');

    // Render with null streamingToken — simulates state after step_complete clears it
    vi.spyOn(goalStreamModule, 'useGoalStream').mockReturnValue({
      connected: true,
      streamingToken: null,
      events: [],
    });

    renderGoalDetailPage();

    // Goal loads and renders; streaming panel must be absent
    await waitFor(() => expect(screen.getByText('Fix prod')).toBeInTheDocument());
    expect(screen.queryByRole('status', { name: /live llm output/i })).not.toBeInTheDocument();
  });
});
