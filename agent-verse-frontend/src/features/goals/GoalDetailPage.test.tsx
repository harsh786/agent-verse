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
  return render(
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

function mockCompletedGoalWithResultArtifact() {
  return vi.spyOn(globalThis, 'fetch').mockImplementation(async (input) => {
    const url = String(input);
    if (url.endsWith('/goals/goal-1/replay')) {
      return new Response(
        JSON.stringify({
          timeline: [
            {
              event_id: 'event-1',
              goal_id: 'goal-1',
              type: 'goal_complete',
              payload: { message: 'Persisted completion event' },
              created_at: '2026-07-01T12:00:00Z',
            },
          ],
        }),
        { status: 200, headers: { 'Content-Type': 'application/json' } }
      );
    }

    return new Response(
      JSON.stringify({
        id: 'goal-1',
        goal_id: 'goal-1',
        status: 'complete',
        goal: 'Fetch Jira issues',
        result_artifact: {
          version: 1,
          kind: 'table',
          title: 'Jira issues assigned to you',
          summary: 'Found 1 Jira issue assigned to you.',
          status: 'success',
          metrics: [{ label: 'Issues', value: 1 }],
          tables: [
            {
              title: 'Issues',
              columns: [
                { key: 'key', label: 'Key', type: 'link' },
                { key: 'summary', label: 'Summary', type: 'text' },
                { key: 'status', label: 'Status', type: 'badge' },
              ],
              rows: [{ key: 'PCF-58608', summary: 'Deployment fix', status: 'Open' }],
            },
          ],
          evidence: {
            tools: [{ name: 'jira_search_issues', server_id: 'jira', success: true }],
            verification: 'Jira returned matching issues.',
          },
          downloads: ['json', 'csv', 'markdown'],
          debug: { event_count: 3 },
        },
      }),
      { status: 200, headers: { 'Content-Type': 'application/json' } }
    );
  });
}

function mockCompletedGoalWithoutResultArtifact() {
  return vi.spyOn(globalThis, 'fetch').mockImplementation(async () =>
    new Response(
      JSON.stringify({
        id: 'goal-1',
        goal_id: 'goal-1',
        status: 'complete',
        goal: 'Fetch Jira issues',
      }),
      { status: 200, headers: { 'Content-Type': 'application/json' } }
    )
  );
}

function mockFailedGoalWithResultArtifact() {
  return vi.spyOn(globalThis, 'fetch').mockImplementation(async () =>
    new Response(
      JSON.stringify({
        id: 'goal-1',
        goal_id: 'goal-1',
        status: 'failed',
        goal: 'Fetch Jira issues',
        result_artifact: {
          version: 1,
          kind: 'error',
          title: 'Jira lookup failed',
          summary: 'The agent could not complete the Jira lookup.',
          status: 'failed',
          metrics: [],
          tables: [],
          evidence: {
            tools: [{ name: 'jira_search_issues', server_id: 'jira', success: true }],
            verification: 'Token expired while creating the PR.',
          },
          downloads: [],
          debug: { event_count: 5 },
        },
      }),
      { status: 200, headers: { 'Content-Type': 'application/json' } }
    )
  );
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

  test('does not leave successful completed goal events spinning', async () => {
    mockGoal('complete');
    vi.spyOn(goalStreamModule, 'useGoalStream').mockReturnValue({
      connected: false,
      streamingToken: null,
      events: [
        { type: 'worker_started', goal: 'Fix prod', worker: 'celery' },
        { type: 'goal_started', goal: 'Fix prod' },
        { type: 'plan_ready', steps: ['Execute the goal autonomously'] },
        { type: 'step_started', step: 'Execute the goal autonomously' },
        { type: 'step_complete', step: 'Execute the goal autonomously', output: 'Done' },
        { type: 'verification_done', success: true, reason: 'Completed by worker' },
        { type: 'goal_complete' },
        { type: 'worker_complete', status: 'complete', iterations: 1 },
      ],
    });

    const { container } = renderGoalDetailPage();

    expect((await screen.findAllByText('worker complete')).length).toBeGreaterThan(0);
    expect(container.querySelectorAll('.animate-spin')).toHaveLength(0);
  });

  test('shows goal text and status badge in header', async () => {
    mockGoal('executing', 'Fix production bug');
    renderGoalDetailPage();
    expect(await screen.findByText('Fix production bug')).toBeInTheDocument();
    expect(screen.getByText('executing')).toBeInTheDocument();
  });

  test('shows completed result artifact by default and opens execution and developer log tabs', async () => {
    mockCompletedGoalWithResultArtifact();
    renderGoalDetailPage();

    expect(await screen.findByText('PCF-58608')).toBeInTheDocument();
    expect(screen.getByText('Deployment fix')).toBeInTheDocument();

    await userEvent.click(screen.getByRole('tab', { name: /execution/i }));
    expect(screen.getByText('Pipeline steps')).toBeInTheDocument();
    expect(screen.getByText('jira.search succeeded')).toBeInTheDocument();

    await userEvent.click(screen.getByRole('tab', { name: /developer log/i }));
    expect(await screen.findByText('goal_complete')).toBeInTheDocument();
    expect(screen.getByText('Persisted completion event')).toBeInTheDocument();
  });

  test('hides artifact tabs and defaults to execution when completed goal has no artifact', async () => {
    mockCompletedGoalWithoutResultArtifact();
    renderGoalDetailPage();

    expect(await screen.findByRole('tabpanel', { name: /execution/i })).toBeInTheDocument();
    expect(screen.getByText('Pipeline steps')).toBeInTheDocument();
    expect(screen.queryByRole('tab', { name: /results/i })).not.toBeInTheDocument();
    expect(screen.queryByRole('tab', { name: /evidence/i })).not.toBeInTheDocument();
    expect(screen.getByRole('tab', { name: /execution/i })).toHaveAttribute('aria-controls', 'goal-tabpanel-execution');
  });

  test('opens execution tab from failed result artifact diagnostic action', async () => {
    mockFailedGoalWithResultArtifact();
    renderGoalDetailPage();

    const openExecutionButton = await screen.findByRole('button', { name: /open execution tab/i });
    await userEvent.click(openExecutionButton);

    expect(screen.getByRole('tabpanel', { name: /execution/i })).toBeInTheDocument();
    expect(screen.getByText('Pipeline steps')).toBeInTheDocument();
  });

  test('supports arrow key navigation across visible tabs', async () => {
    mockCompletedGoalWithResultArtifact();
    renderGoalDetailPage();

    const resultsTab = await screen.findByRole('tab', { name: /results/i });
    const evidenceTab = screen.getByRole('tab', { name: /evidence/i });
    const executionTab = screen.getByRole('tab', { name: /execution/i });
    const evalTab = screen.getByRole('tab', { name: /eval/i });

    resultsTab.focus();
    await userEvent.keyboard('{ArrowRight}');

    expect(evidenceTab).toHaveFocus();
    expect(evidenceTab).toHaveAttribute('aria-selected', 'true');

    await userEvent.keyboard('{ArrowRight}');
    expect(executionTab).toHaveFocus();
    expect(executionTab).toHaveAttribute('aria-selected', 'true');

    await userEvent.keyboard('{ArrowLeft}');
    expect(evidenceTab).toHaveFocus();
    expect(evidenceTab).toHaveAttribute('aria-selected', 'true');

    await userEvent.keyboard('{End}');
    expect(evalTab).toHaveFocus();
    expect(evalTab).toHaveAttribute('aria-selected', 'true');

    await userEvent.keyboard('{Home}');
    expect(resultsTab).toHaveFocus();
    expect(resultsTab).toHaveAttribute('aria-selected', 'true');
  });

  test('focuses and selects execution tab from failed result artifact diagnostic action', async () => {
    mockFailedGoalWithResultArtifact();
    renderGoalDetailPage();

    const openExecutionButton = await screen.findByRole('button', { name: /open execution tab/i });
    openExecutionButton.focus();
    await userEvent.keyboard('{Enter}');

    const executionTab = screen.getByRole('tab', { name: /execution/i });
    expect(executionTab).toHaveFocus();
    expect(executionTab).toHaveAttribute('aria-selected', 'true');
    expect(screen.getByRole('tabpanel', { name: /execution/i })).toBeInTheDocument();
  });

  test('renders developer log events returned with ts and data replay shape', async () => {
    vi.spyOn(globalThis, 'fetch').mockImplementation(async (input) => {
      const url = String(input);
      if (url.endsWith('/goals/goal-1/replay')) {
        return new Response(
          JSON.stringify({
            timeline: [
              {
                event_id: 'event-1',
                goal_id: 'goal-1',
                type: 'goal_complete',
                data: { message: 'Persisted completion event from replay data' },
                ts: '2026-07-01T12:00:00Z',
              },
            ],
          }),
          { status: 200, headers: { 'Content-Type': 'application/json' } }
        );
      }

      return new Response(
        JSON.stringify({ id: 'goal-1', goal_id: 'goal-1', status: 'complete', goal: 'Fetch Jira issues' }),
        { status: 200, headers: { 'Content-Type': 'application/json' } }
      );
    });

    renderGoalDetailPage();
    await userEvent.click(await screen.findByRole('tab', { name: /developer log/i }));

    expect(await screen.findByText('goal_complete')).toBeInTheDocument();
    expect(screen.getByText('Persisted completion event from replay data')).toBeInTheDocument();
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
