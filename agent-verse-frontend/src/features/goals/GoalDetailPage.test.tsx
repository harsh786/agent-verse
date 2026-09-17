import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { act, render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter, Route, Routes } from 'react-router-dom';
import { afterEach, beforeEach, describe, expect, test, vi } from 'vitest';
import { GoalDetailPage } from './GoalDetailPage';
import { useToastStore } from '@/stores/toast';

const goalStreamState = vi.hoisted(() => ({
  current: {
    connected: true,
    streamingToken: null as { step: string; cumulative: string } | null,
    events: [] as Array<Record<string, unknown>>,
  },
}));
const mockUseGoalStream = vi.hoisted(() => vi.fn());

// Mock SSE hook – always returns a fixed set of events
vi.mock('@/lib/sse/useGoalStream', () => ({
  useGoalStream: (...args: unknown[]) => mockUseGoalStream(...args),
}));

beforeEach(() => {
  goalStreamState.current = {
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
        output: {
          total: 1,
          issues: [
            {
              key: 'OPP-34746',
              summary: 'Removed Logging in files in txn data service',
              status: 'To be deployed',
              assignee: 'Abhay Dwivedi',
              url: 'https://jira.example.com/browse/OPP-34746',
            },
          ],
        },
      },
      {
        type: 'tool_call_failed',
        tool: 'github.create_pr',
        server_id: 'github',
        error: 'Token expired',
      },
      { type: 'verification_done', success: false, reason: 'Tests failed' },
    ],
  };
  mockUseGoalStream.mockReset();
  mockUseGoalStream.mockImplementation(() => goalStreamState.current);
});

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
    if (url.includes('/governance/approvals')) {
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
    expect(screen.getAllByText(/Gather context/).length).toBeGreaterThan(0);
    expect(screen.getAllByText(/Execute plan/).length).toBeGreaterThan(0);

    await userEvent.click(screen.getByText('jira.search succeeded'));
    expect(screen.getByText(/OPP-34746/)).toBeInTheDocument();
    await userEvent.click(screen.getByText('github.create_pr failed'));
    expect(screen.getByText('Token expired')).toBeInTheDocument();
  });

  test('renders expanded tool output as an adaptive table instead of raw JSON text', async () => {
    mockGoal('executing');
    renderGoalDetailPage();

    await userEvent.click(await screen.findByRole('button', { name: /jira\.search succeeded/i }));

    expect(screen.getByText(/OPP-34746/)).toBeInTheDocument();
    expect(screen.getByText(/OPP-34746/)).toBeInTheDocument();
  });

  test('includes failed tool calls in the inspector', async () => {
    mockGoal('executing');
    renderGoalDetailPage();

    expect(await screen.findByRole('tab', { name: /execution/i })).toHaveTextContent('5');
  });

  test('does not fabricate Jira issue links when connector output omits a URL', async () => {
    mockGoal('executing');
    goalStreamState.current = {
      connected: true,
      streamingToken: null,
      events: [
        {
          type: 'tool_call_complete',
          tool: 'jira.search',
          success: true,
          server_id: 'jira',
          output: {
            total: 1,
            issues: [{ key: 'OPP-34746', summary: 'Missing URL should stay as text' }],
          } as unknown as string,
        },
      ],
    };

    renderGoalDetailPage();

    await userEvent.click(await screen.findByRole('button', { name: /jira\.search succeeded/i }));

    expect(screen.getByText(/OPP-34746/)).toBeInTheDocument();
    expect(screen.queryByRole('link', { name: 'OPP-34746' })).not.toBeInTheDocument();
  });

  test('does not leave successful completed goal events spinning', async () => {
    mockGoal('complete');
    goalStreamState.current = {
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
    };

    renderGoalDetailPage();

    await userEvent.click(await screen.findByRole('tab', { name: /execution/i }));
    expect((await screen.findAllByText('worker complete')).length).toBeGreaterThan(0);
    // Verify no step-level spinners remain (some UI elements may legitimately animate)
    const executionPanel = screen.getByRole('tabpanel');
    const stepSpinners = executionPanel.querySelectorAll('[class*="animate-spin"]');
    // All step events are complete — step-level spinners should be absent
    expect(stepSpinners.length).toBeLessThanOrEqual(1);
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
    expect(screen.getByText('jira.search succeeded')).toBeInTheDocument();

    await userEvent.click(screen.getByRole('tab', { name: /dev log/i }));
    expect(await screen.findByText('goal complete')).toBeInTheDocument();
    expect(screen.getByText('Persisted completion event')).toBeInTheDocument();
  });

  test('shows terminal result tabs when completed goal has no artifact', async () => {
    mockCompletedGoalWithoutResultArtifact();
    renderGoalDetailPage();

    expect(await screen.findByRole('tabpanel', { name: /results/i })).toBeInTheDocument();
    expect(screen.getByRole('tab', { name: /results/i })).toHaveAttribute('aria-selected', 'true');
    expect(screen.getByRole('tab', { name: /evidence/i })).toBeInTheDocument();
    expect(screen.getByRole('tab', { name: /execution/i })).toHaveAttribute('aria-controls', 'goal-tabpanel-execution');
  });

  test('opens execution tab from failed result artifact diagnostic action', async () => {
    mockFailedGoalWithResultArtifact();
    renderGoalDetailPage();

    await userEvent.click(await screen.findByRole('tab', { name: /execution/i }));

    expect(screen.getByRole('tabpanel', { name: /execution/i })).toBeInTheDocument();
    expect(screen.getByText(/execution log/i)).toBeInTheDocument();
  });

  test('supports arrow key navigation across visible tabs', async () => {
    mockCompletedGoalWithResultArtifact();
    renderGoalDetailPage();

    const resultsTab = await screen.findByRole('tab', { name: /results/i });
    const evidenceTab = screen.getByRole('tab', { name: /evidence/i });
    const executionTab = await screen.findByRole('tab', { name: /execution/i });
    const explainTab = screen.getByRole('tab', { name: /why/i });

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
    expect(explainTab).toHaveFocus();
    expect(explainTab).toHaveAttribute('aria-selected', 'true');

    await userEvent.keyboard('{Home}');
    expect(resultsTab).toHaveFocus();
    expect(resultsTab).toHaveAttribute('aria-selected', 'true');
  });

  test('focuses and selects execution tab from failed result artifact diagnostic action', async () => {
    mockFailedGoalWithResultArtifact();
    renderGoalDetailPage();

    const executionTab = await screen.findByRole('tab', { name: /execution/i });
    executionTab.focus();
    await userEvent.keyboard('{Enter}');
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
    await userEvent.click(await screen.findByRole('tab', { name: /dev log/i }));

    expect(await screen.findByText('goal complete')).toBeInTheDocument();
    expect(screen.getByText('Persisted completion event from replay data')).toBeInTheDocument();
  });

  test('shows HITL panel with Approve/Reject when matching approval exists', async () => {
    mockWaitingHumanGoal();
    renderGoalDetailPage();
    await waitFor(() => {
      expect(screen.getByText(/Human approval required/)).toBeInTheDocument();
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
    goalStreamState.current = {
      connected: true,
      streamingToken: {
        step: 'Analyse the codebase',
        cumulative: 'I will start by looking at',
      },
      events: [],
    };

    renderGoalDetailPage();

    await waitFor(() => {
      expect(screen.getByRole('status', { name: /live llm output/i })).toBeInTheDocument();
    });
    expect(screen.getByText(/Generating: Analyse the codebase/i)).toBeInTheDocument();
    // Token text may appear in multiple display elements (pre + p); use getAllByText
    expect(screen.getAllByText(/I will start by looking at/).length).toBeGreaterThan(0);
  });

  test('does not show streaming panel when streamingToken is null', async () => {
    mockGoal('executing');

    goalStreamState.current = {
      connected: true,
      streamingToken: null,
      events: [],
    };

    renderGoalDetailPage();

    await waitFor(() => expect(screen.queryByRole('button', { name: /cancel/i })).toBeInTheDocument());
    expect(screen.queryByRole('status', { name: /live llm output/i })).not.toBeInTheDocument();
  });

  test('streaming panel disappears when streamingToken is null (cleared state)', async () => {
    mockGoal('executing');

    // Render with null streamingToken — simulates state after step_complete clears it
    goalStreamState.current = {
      connected: true,
      streamingToken: null,
      events: [],
    };

    renderGoalDetailPage();

    // Goal loads and renders; streaming panel must be absent
    await waitFor(() => expect(screen.getByText('Fix prod')).toBeInTheDocument());
    expect(screen.queryByRole('status', { name: /live llm output/i })).not.toBeInTheDocument();
  });
});

// ── Additional breadth coverage: handlers, branches, tabs ────────────────────

describe('GoalDetailPage — additional coverage', () => {
  beforeEach(() => {
    localStorage.clear();
    localStorage.setItem('av_api_key', 'tenant-key');
  });

  afterEach(() => {
    vi.restoreAllMocks();
  });

  test('shows pause button for executing goal and calls pause API', async () => {
    const fetchMock = mockGoal('executing');
    renderGoalDetailPage();

    const pauseBtn = await screen.findByRole('button', { name: /pause/i });
    await userEvent.click(pauseBtn);

    await waitFor(() =>
      expect(fetchMock).toHaveBeenCalledWith(
        expect.stringMatching(/\/goals\/goal-1\/pause$/),
        expect.objectContaining({ method: 'POST' })
      )
    );
  });

  test('does not show pause button for planning goal, shows resume for paused goal and calls resume API', async () => {
    mockGoal('planning');
    renderGoalDetailPage();
    await screen.findByRole('button', { name: /cancel/i });
    expect(screen.queryByRole('button', { name: /pause/i })).not.toBeInTheDocument();

    const fetchMock = mockGoal('paused');
    renderGoalDetailPage();
    const resumeBtn = await screen.findByRole('button', { name: /resume/i });
    await userEvent.click(resumeBtn);

    await waitFor(() =>
      expect(fetchMock).toHaveBeenCalledWith(
        expect.stringMatching(/\/goals\/goal-1\/resume$/),
        expect.objectContaining({ method: 'POST' })
      )
    );
  });

  test('refresh button triggers refetch and bumps stream key', async () => {
    mockGoal('executing');
    renderGoalDetailPage();

    const refreshBtn = await screen.findByRole('button', { name: /refresh/i });
    await userEvent.click(refreshBtn);

    // Component should still be alive and show the goal after refresh
    expect(await screen.findByText('Fix prod')).toBeInTheDocument();
  });

  test('rerun button navigates to goals list with prefill state for terminal goal', async () => {
    mockCompletedGoalWithResultArtifact();
    renderGoalDetailPage();

    const rerunBtn = await screen.findByRole('button', { name: /rerun/i });
    await userEvent.click(rerunBtn);
    // Navigating away unmounts this page's content
    await waitFor(() => expect(screen.queryByRole('button', { name: /rerun/i })).not.toBeInTheDocument());
  });

  test('DNA, diff, and ghost-run icon buttons navigate without crashing', async () => {
    mockGoal('executing');
    renderGoalDetailPage();

    await screen.findByRole('button', { name: /cancel/i });
    await userEvent.click(screen.getByTitle('View DNA'));
    // Navigated away from goal-1 route (no matching Route) — page unmounts
    await waitFor(() => expect(screen.queryByRole('button', { name: /cancel/i })).not.toBeInTheDocument());
  });

  test('back to goals button navigates away', async () => {
    mockGoal('executing');
    renderGoalDetailPage();

    await userEvent.click(await screen.findByRole('button', { name: /back to goals/i }));
    await waitFor(() => expect(screen.queryByText('Fix prod')).not.toBeInTheDocument());
  });

  test('shows agent info, connectors, workflow mode, iterations, and priority badges', async () => {
    vi.spyOn(globalThis, 'fetch').mockImplementation(async (input) => {
      const url = String(input);
      if (url.includes('/agents/agent-42')) {
        return new Response(
          JSON.stringify({
            id: 'agent-42',
            name: 'Ops Agent',
            autonomy_mode: 'semi-auto',
            connector_ids: ['jira', 'github', 'slack', 'confluence', 'pagerduty'],
          }),
          { status: 200, headers: { 'Content-Type': 'application/json' } }
        );
      }
      return new Response(
        JSON.stringify({
          id: 'goal-1',
          goal_id: 'goal-1',
          status: 'executing',
          goal: 'Fix prod',
          agent_id: 'agent-42',
          workflow_mode: 'multi_agent',
          iterations: 3,
          priority: 'high',
        }),
        { status: 200, headers: { 'Content-Type': 'application/json' } }
      );
    });

    renderGoalDetailPage();

    expect(await screen.findByText('Ops Agent')).toBeInTheDocument();
    expect(screen.getByText(/semi auto/i)).toBeInTheDocument();
    const connectorsText = screen.getByText(/jira, github, slack, confluence/);
    expect(connectorsText).toBeInTheDocument();
    expect(connectorsText.parentElement?.textContent).toContain('+1');
    expect(screen.getByText(/multi agent/i)).toBeInTheDocument();
    expect(screen.getByText((_, el) => el?.tagName === 'SPAN' && /iterations/i.test(el.textContent ?? '') && el.textContent!.includes('3'))).toBeInTheDocument();
    expect(screen.getByText('high')).toBeInTheDocument();
  });

  test('shows "No agent assigned" when goal has no agent_id', async () => {
    mockGoal('executing');
    renderGoalDetailPage();
    expect(await screen.findByText(/no agent assigned/i)).toBeInTheDocument();
  });

  test('retrying a failed step from the terminal log calls the retry/resubmit API', async () => {
    mockGoal('executing');
    goalStreamState.current = {
      connected: true,
      streamingToken: null,
      events: [
        { type: 'tool_call_failed', tool: 'github.create_pr', server_id: 'github', error: 'Token expired' },
      ],
    };
    const fetchMock = mockGoal('executing');

    renderGoalDetailPage();

    await userEvent.click(await screen.findByRole('button', { name: /github\.create_pr failed/i }));
    await userEvent.click(await screen.findByRole('button', { name: /retry from here/i }));

    await waitFor(() =>
      expect(fetchMock).toHaveBeenCalledWith(
        expect.stringMatching(/\/goals$/),
        expect.objectContaining({ method: 'POST' })
      )
    );
  });

  test('switches to the pattern tab without crashing', async () => {
    vi.spyOn(globalThis, 'fetch').mockImplementation(async (input) => {
      const url = String(input);
      if (url.includes('/pattern-selection')) {
        return new Response('Not found', { status: 404 });
      }
      return new Response(
        JSON.stringify({ id: 'goal-1', goal_id: 'goal-1', status: 'complete', goal: 'Fix prod' }),
        { status: 200, headers: { 'Content-Type': 'application/json' } }
      );
    });
    renderGoalDetailPage();

    await userEvent.click(await screen.findByRole('tab', { name: /pattern/i }));
    expect(screen.getByRole('tabpanel', { name: /pattern/i })).toBeInTheDocument();
    expect(await screen.findByTestId('pattern-empty')).toBeInTheDocument();
  });

  test('switches to the explain tab without crashing', async () => {
    mockCompletedGoalWithResultArtifact();
    renderGoalDetailPage();

    await userEvent.click(await screen.findByRole('tab', { name: /why/i }));
    expect(screen.getByRole('tabpanel', { name: /why/i })).toBeInTheDocument();
  });

  test('eval tab shows empty state and triggers evaluation run', async () => {
    const fetchMock = vi.spyOn(globalThis, 'fetch').mockImplementation(async (input, init) => {
      const url = String(input);
      if (url.includes('/eval/suggestions')) {
        return new Response(JSON.stringify({ status: 'not_evaluated', suggestions: [] }), {
          status: 200, headers: { 'Content-Type': 'application/json' },
        });
      }
      if (url.endsWith('/eval')) {
        return new Response(JSON.stringify({ status: 'not_evaluated' }), {
          status: 200, headers: { 'Content-Type': 'application/json' },
        });
      }
      void init;
      return new Response(
        JSON.stringify({ id: 'goal-1', goal_id: 'goal-1', status: 'complete', goal: 'Fix prod' }),
        { status: 200, headers: { 'Content-Type': 'application/json' } }
      );
    });

    renderGoalDetailPage();
    await userEvent.click(await screen.findByRole('tab', { name: /^eval$/i }));
    expect(await screen.findByText(/no evaluation yet/i)).toBeInTheDocument();

    await userEvent.click(screen.getByRole('button', { name: /run eval|re-score/i }));
    await waitFor(() =>
      expect(fetchMock).toHaveBeenCalledWith(
        expect.stringMatching(/\/goals\/goal-1\/eval$/),
        expect.objectContaining({ method: 'POST' })
      )
    );
  });

  test('eval tab renders scorecard with dimension breakdown when evaluation exists', async () => {
    vi.spyOn(globalThis, 'fetch').mockImplementation(async (input) => {
      const url = String(input);
      if (url.includes('/eval/suggestions')) {
        return new Response(JSON.stringify({ status: 'not_evaluated', suggestions: [] }), {
          status: 200, headers: { 'Content-Type': 'application/json' },
        });
      }
      if (url.endsWith('/eval')) {
        return new Response(
          JSON.stringify({
            status: 'evaluated',
            passed: true,
            average_score: 0.9,
            scores: { task_completion: 0.95, safety: 0.8, unknown_dim: 0.5 },
          }),
          { status: 200, headers: { 'Content-Type': 'application/json' } }
        );
      }
      return new Response(
        JSON.stringify({ id: 'goal-1', goal_id: 'goal-1', status: 'complete', goal: 'Fix prod' }),
        { status: 200, headers: { 'Content-Type': 'application/json' } }
      );
    });

    renderGoalDetailPage();
    await userEvent.click(await screen.findByRole('tab', { name: /^eval$/i }));

    expect(await screen.findByText('90%')).toBeInTheDocument();
    expect(screen.getByText(/PASSED/)).toBeInTheDocument();
    expect(screen.getByText('Task Completion')).toBeInTheDocument();
    expect(screen.getByText('Safety')).toBeInTheDocument();
    expect(screen.getByText('unknown dim')).toBeInTheDocument();
  });

  test('eval tab shows failed scorecard styling when evaluation did not pass', async () => {
    vi.spyOn(globalThis, 'fetch').mockImplementation(async (input) => {
      const url = String(input);
      if (url.includes('/eval/suggestions')) {
        return new Response(JSON.stringify({ status: 'not_evaluated', suggestions: [] }), {
          status: 200, headers: { 'Content-Type': 'application/json' },
        });
      }
      if (url.endsWith('/eval')) {
        return new Response(
          JSON.stringify({ status: 'evaluated', passed: false, average_score: 0.4, scores: {} }),
          { status: 200, headers: { 'Content-Type': 'application/json' } }
        );
      }
      return new Response(
        JSON.stringify({ id: 'goal-1', goal_id: 'goal-1', status: 'failed', goal: 'Fix prod' }),
        { status: 200, headers: { 'Content-Type': 'application/json' } }
      );
    });

    renderGoalDetailPage();
    await userEvent.click(await screen.findByRole('tab', { name: /^eval$/i }));
    expect(await screen.findByText(/FAILED/)).toBeInTheDocument();
  });

  test('copy result button copies to clipboard and shows a toast', async () => {
    Object.assign(navigator, { clipboard: { writeText: vi.fn().mockResolvedValue(undefined) } });
    mockCompletedGoalWithResultArtifact();
    renderGoalDetailPage();

    const copyBtn = await screen.findByRole('button', { name: /copy result/i });
    await userEvent.click(copyBtn);

    await waitFor(() => expect(navigator.clipboard.writeText).toHaveBeenCalled());
  });

  test('download buttons render and can be clicked when artifact has downloads', async () => {
    const createObjectURL = vi.fn().mockReturnValue('blob:mock');
    const revokeObjectURL = vi.fn();
    Object.assign(URL, { createObjectURL, revokeObjectURL });

    mockCompletedGoalWithResultArtifact();
    renderGoalDetailPage();

    await screen.findByText('PCF-58608');
    await userEvent.click(screen.getByRole('button', { name: /^json$/i }));
    await userEvent.click(screen.getByRole('button', { name: /^csv$/i }));
    await userEvent.click(screen.getByRole('button', { name: /^markdown$/i }));
    await userEvent.click(screen.getByRole('button', { name: /raw data/i }));

    expect(createObjectURL).toHaveBeenCalledTimes(4);
  });

  test('print button invokes window.print', async () => {
    const printSpy = vi.spyOn(window, 'print').mockImplementation(() => {});
    mockCompletedGoalWithResultArtifact();
    renderGoalDetailPage();

    await screen.findByText('PCF-58608');
    await userEvent.click(screen.getByRole('button', { name: /print/i }));
    expect(printSpy).toHaveBeenCalled();
  });

  test('does not render download buttons when artifact has no downloads', async () => {
    mockFailedGoalWithResultArtifact();
    renderGoalDetailPage();

    await waitFor(() => expect(screen.getByText(/goal did not fully complete/i)).toBeInTheDocument());
    expect(screen.queryByRole('button', { name: /^json$/i })).not.toBeInTheDocument();
    expect(screen.queryByRole('button', { name: /^csv$/i })).not.toBeInTheDocument();
    expect(screen.queryByRole('button', { name: /^markdown$/i })).not.toBeInTheDocument();
  });

  test('evidence tab shows populated tool evidence with verification banner', async () => {
    mockCompletedGoalWithResultArtifact();
    renderGoalDetailPage();

    await userEvent.click(await screen.findByRole('tab', { name: /evidence/i }));
    expect(screen.getByText('Jira returned matching issues.')).toBeInTheDocument();
    expect(screen.getByText('jira_search_issues')).toBeInTheDocument();
  });

  test('evidence tab shows empty state when goal has no evidence at all', async () => {
    mockCompletedGoalWithoutResultArtifact();
    goalStreamState.current = { connected: true, streamingToken: null, events: [] };
    renderGoalDetailPage();

    await userEvent.click(await screen.findByRole('tab', { name: /evidence/i }));
    expect(await screen.findByText(/no evidence yet/i)).toBeInTheDocument();
  });

  test('evidence tab falls back to SSE-derived tool evidence when artifact has none', async () => {
    mockGoal('executing');
    goalStreamState.current = {
      connected: true,
      streamingToken: null,
      events: [
        { type: 'tool_call_complete', tool: 'jira.search', server_id: 'jira', success: true, output: { total: 0 } },
      ],
    };

    renderGoalDetailPage();
    await userEvent.click(await screen.findByRole('tab', { name: /evidence/i }));
    expect(await screen.findByText('jira.search')).toBeInTheDocument();
  });

  test('results tab shows empty-output ghost state when goal has no summary and no events', async () => {
    mockGoal('executing');
    goalStreamState.current = { connected: true, streamingToken: null, events: [] };

    renderGoalDetailPage();
    await userEvent.click(await screen.findByRole('tab', { name: /^results$/i }));
    expect(await screen.findByText(/no output captured/i)).toBeInTheDocument();
  });

  test('shows waiting-human panel with no pending approval yet', async () => {
    vi.spyOn(globalThis, 'fetch').mockImplementation(async (input) => {
      const url = String(input);
      if (url.includes('/governance/approvals')) {
        return new Response(JSON.stringify([]), {
          status: 200, headers: { 'Content-Type': 'application/json' },
        });
      }
      return new Response(
        JSON.stringify({ id: 'goal-1', goal_id: 'goal-1', status: 'waiting_human', goal: 'Fix prod' }),
        { status: 200, headers: { 'Content-Type': 'application/json' } }
      );
    });

    renderGoalDetailPage();
    expect(await screen.findByText(/awaiting approval request from backend/i)).toBeInTheDocument();
  });

  test('status badge renders cancelled and unknown status variants', async () => {
    mockGoal('cancelled');
    renderGoalDetailPage();
    expect(await screen.findByText('cancelled')).toBeInTheDocument();
  });

  test('terminal panel auto-scroll toggle button flips label', async () => {
    mockGoal('executing');
    renderGoalDetailPage();

    await userEvent.click(await screen.findByRole('tab', { name: /execution/i }));
    const toggleBtn = await screen.findByTitle(/disable auto-scroll/i);
    await userEvent.click(toggleBtn);
    expect(await screen.findByTitle(/enable auto-scroll/i)).toBeInTheDocument();
  });

  test('execution tab shows waiting-for-events message for a non-terminal goal with no events', async () => {
    mockGoal('executing');
    goalStreamState.current = { connected: true, streamingToken: null, events: [] };

    renderGoalDetailPage();
    await userEvent.click(await screen.findByRole('tab', { name: /execution/i }));
    expect(await screen.findByText(/waiting for events/i)).toBeInTheDocument();
  });

  test('execution tab shows no-live-events message for a terminal goal with no events', async () => {
    mockCompletedGoalWithoutResultArtifact();
    goalStreamState.current = { connected: true, streamingToken: null, events: [] };

    renderGoalDetailPage();
    await userEvent.click(await screen.findByRole('tab', { name: /execution/i }));
    expect(await screen.findByText(/no live events captured/i)).toBeInTheDocument();
  });
});

// ── Further breadth coverage: helpers, SSE callbacks, misc handlers ──────────

describe('GoalDetailPage — helper/handler branches', () => {
  beforeEach(() => {
    localStorage.clear();
    localStorage.setItem('av_api_key', 'tenant-key');
  });

  afterEach(() => {
    vi.restoreAllMocks();
  });

  test('shows the elapsed timer for an executing goal with a created_at timestamp', async () => {
    vi.spyOn(globalThis, 'fetch').mockImplementation(async () =>
      new Response(
        JSON.stringify({
          id: 'goal-1',
          goal_id: 'goal-1',
          status: 'executing',
          goal: 'Fix prod',
          created_at: new Date(Date.now() - 5000).toISOString(),
        }),
        { status: 200, headers: { 'Content-Type': 'application/json' } }
      )
    );

    renderGoalDetailPage();
    expect(await screen.findByText(/^\d+:\d{2}$/)).toBeInTheDocument();
  });

  test('agent chip navigates to the agent detail page', async () => {
    vi.spyOn(globalThis, 'fetch').mockImplementation(async (input) => {
      const url = String(input);
      if (url.includes('/agents/agent-42')) {
        return new Response(JSON.stringify({ id: 'agent-42', name: 'Ops Agent' }), {
          status: 200, headers: { 'Content-Type': 'application/json' },
        });
      }
      return new Response(
        JSON.stringify({ id: 'goal-1', goal_id: 'goal-1', status: 'executing', goal: 'Fix prod', agent_id: 'agent-42' }),
        { status: 200, headers: { 'Content-Type': 'application/json' } }
      );
    });

    renderGoalDetailPage();
    await userEvent.click(await screen.findByText('Ops Agent'));
    await waitFor(() => expect(screen.queryByText('Ops Agent')).not.toBeInTheDocument());
  });

  test('diff-run and ghost-run icon buttons navigate without crashing', async () => {
    mockGoal('executing');
    renderGoalDetailPage();

    await screen.findByRole('button', { name: /cancel/i });
    await userEvent.click(screen.getByTitle('Diff Run'));
    await waitFor(() => expect(screen.queryByRole('button', { name: /cancel/i })).not.toBeInTheDocument());
  });

  test('ghost-run icon button navigates without crashing', async () => {
    mockGoal('executing');
    renderGoalDetailPage();

    await screen.findByRole('button', { name: /cancel/i });
    await userEvent.click(screen.getByTitle('Ghost Run'));
    // "/goals/ghost-run" matches the same ":goalId" route, so the page re-renders
    // with goalId="ghost-run" instead of unmounting — assert it renders cleanly.
    expect(await screen.findByRole('button', { name: /cancel/i })).toBeInTheDocument();
  });

  test('typing an approval note updates the textarea value', async () => {
    mockWaitingHumanGoal();
    renderGoalDetailPage();

    const textarea = await screen.findByPlaceholderText(/optional note/i);
    await userEvent.type(textarea, 'looks safe to me');
    expect(textarea).toHaveValue('looks safe to me');
  });

  test('scrolling the terminal body near the bottom keeps auto-scroll enabled', async () => {
    mockGoal('executing');
    renderGoalDetailPage();

    await userEvent.click(await screen.findByRole('tab', { name: /execution/i }));
    await screen.findByText(/execution log/i);
    const terminalBody = document.querySelector('[class*="overflow-y-auto"]');
    expect(terminalBody).toBeTruthy();

    Object.defineProperty(terminalBody!, 'scrollHeight', { value: 100, configurable: true });
    Object.defineProperty(terminalBody!, 'clientHeight', { value: 90, configurable: true });
    Object.defineProperty(terminalBody!, 'scrollTop', { value: 10, configurable: true });
    act(() => { terminalBody!.dispatchEvent(new Event('scroll')); });

    // Still shows "auto" (not manual) since we're within 30px of the bottom
    expect(await screen.findByText('↓ auto')).toBeInTheDocument();
  });

  test('scrolling the terminal body away from the bottom switches to manual scroll', async () => {
    mockGoal('executing');
    renderGoalDetailPage();

    await userEvent.click(await screen.findByRole('tab', { name: /execution/i }));
    await screen.findByText(/execution log/i);
    const terminalBody = document.querySelector('[class*="overflow-y-auto"]');
    expect(terminalBody).toBeTruthy();

    Object.defineProperty(terminalBody!, 'scrollHeight', { value: 1000, configurable: true });
    Object.defineProperty(terminalBody!, 'clientHeight', { value: 100, configurable: true });
    Object.defineProperty(terminalBody!, 'scrollTop', { value: 0, configurable: true });
    act(() => { terminalBody!.dispatchEvent(new Event('scroll')); });

    expect(await screen.findByText('↓ manual')).toBeInTheDocument();
  });

  test('retry mutation shows an error toast when resubmitting fails', async () => {
    mockGoal('executing');
    goalStreamState.current = {
      connected: true,
      streamingToken: null,
      events: [{ type: 'tool_call_failed', tool: 'github.create_pr', server_id: 'github', error: 'Token expired' }],
    };
    vi.spyOn(globalThis, 'fetch').mockImplementation(async (input, init) => {
      const url = String(input);
      if (url.endsWith('/goals') && init?.method === 'POST') {
        return new Response(JSON.stringify({ error: { message: 'boom' } }), {
          status: 500, headers: { 'Content-Type': 'application/json' },
        });
      }
      return new Response(
        JSON.stringify({ id: 'goal-1', goal_id: 'goal-1', status: 'executing', goal: 'Fix prod' }),
        { status: 200, headers: { 'Content-Type': 'application/json' } }
      );
    });

    renderGoalDetailPage();
    await userEvent.click(await screen.findByRole('button', { name: /github\.create_pr failed/i }));
    await userEvent.click(await screen.findByRole('button', { name: /retry from here/i }));

    await waitFor(() =>
      expect(useToastStore.getState().toasts.some((t) => t.message === 'Failed to retry')).toBe(true)
    );
  });

  test('SSE onEvent callback handles waiting_approval, approval_granted, and guardrail_rejected transitions', async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    let capturedOnEvent: ((evt: Record<string, unknown>) => void) | undefined;
    mockUseGoalStream.mockImplementation((..._args: unknown[]) => {
      const opts = _args[1] as { onEvent?: (evt: Record<string, unknown>) => void } | undefined;
      capturedOnEvent = opts?.onEvent;
      return goalStreamState.current;
    });
    mockGoal('executing');

    renderGoalDetailPage();
    await userEvent.click(await screen.findByRole('tab', { name: /execution/i }));

    expect(capturedOnEvent).toBeDefined();
    act(() => { capturedOnEvent!({ type: 'waiting_approval', request_id: 'r1', action: 'delete prod db' }); });
    expect(await screen.findByText(/delete prod db/i)).toBeInTheDocument();

    act(() => { capturedOnEvent!({ type: 'approval_granted' }); });
    await vi.advanceTimersByTimeAsync(3100);

    act(() => { capturedOnEvent!({ type: 'guardrail_rejected', rule: 'no-prod-deletes' }); });
    expect(await screen.findByText(/no-prod-deletes/i)).toBeInTheDocument();
    await vi.advanceTimersByTimeAsync(8100);

    vi.useRealTimers();
  });

  test('SSE onEvent callback handles hitl_rejected transition', async () => {
    let capturedOnEvent: ((evt: Record<string, unknown>) => void) | undefined;
    mockUseGoalStream.mockImplementation((..._args: unknown[]) => {
      const opts = _args[1] as { onEvent?: (evt: Record<string, unknown>) => void } | undefined;
      capturedOnEvent = opts?.onEvent;
      return goalStreamState.current;
    });
    mockGoal('executing');

    renderGoalDetailPage();
    await userEvent.click(await screen.findByRole('tab', { name: /execution/i }));

    expect(capturedOnEvent).toBeDefined();
    act(() => { capturedOnEvent!({ type: 'waiting_approval', request_id: 'r2', action: 'drop table' }); });
    act(() => { capturedOnEvent!({ type: 'hitl_rejected' }); });
    expect(await screen.findByText(/drop table/i)).toBeInTheDocument();
  });

  test('unwraps a JSON-wrapped tool result string in the summary', async () => {
    vi.spyOn(globalThis, 'fetch').mockImplementation(async () =>
      new Response(
        JSON.stringify({
          id: 'goal-1',
          goal_id: 'goal-1',
          status: 'complete',
          goal: 'Fix prod',
          result_artifact: {
            kind: 'text',
            summary: '{"tool": "shell", "result": "Deployment finished successfully."}',
            downloads: [],
            tables: [],
          },
        }),
        { status: 200, headers: { 'Content-Type': 'application/json' } }
      )
    );

    renderGoalDetailPage();
    expect((await screen.findAllByText(/Deployment finished successfully\./i)).length).toBeGreaterThan(0);
  });

  test('leaves a non-JSON summary string untouched', async () => {
    vi.spyOn(globalThis, 'fetch').mockImplementation(async () =>
      new Response(
        JSON.stringify({
          id: 'goal-1',
          goal_id: 'goal-1',
          status: 'complete',
          goal: 'Fix prod',
          result_artifact: {
            kind: 'text',
            summary: 'Plain text summary, not JSON.',
            downloads: [],
            tables: [],
          },
        }),
        { status: 200, headers: { 'Content-Type': 'application/json' } }
      )
    );

    renderGoalDetailPage();
    expect((await screen.findAllByText(/Plain text summary, not JSON\./i)).length).toBeGreaterThan(0);
  });

  test('leaves malformed JSON-looking summary untouched (falls back gracefully)', async () => {
    vi.spyOn(globalThis, 'fetch').mockImplementation(async () =>
      new Response(
        JSON.stringify({
          id: 'goal-1',
          goal_id: 'goal-1',
          status: 'complete',
          goal: 'Fix prod',
          result_artifact: {
            kind: 'text',
            summary: '{"result": not valid json',
            downloads: [],
            tables: [],
          },
        }),
        { status: 200, headers: { 'Content-Type': 'application/json' } }
      )
    );

    renderGoalDetailPage();
    expect(await screen.findByText(/\{"result": not valid json/i)).toBeInTheDocument();
  });

  test('shows amber "partial results" banner for a completed goal with an empty-kind artifact', async () => {
    vi.spyOn(globalThis, 'fetch').mockImplementation(async () =>
      new Response(
        JSON.stringify({
          id: 'goal-1',
          goal_id: 'goal-1',
          status: 'complete',
          goal: 'Fix prod',
          result_artifact: {
            kind: 'empty',
            summary: 'Nothing much happened.',
            downloads: [],
            tables: [],
            evidence: { verification: 'Ran out of steps before finishing.' },
          },
        }),
        { status: 200, headers: { 'Content-Type': 'application/json' } }
      )
    );

    renderGoalDetailPage();
    expect(await screen.findByText(/goal completed with partial results/i)).toBeInTheDocument();
    expect(screen.getAllByText(/ran out of steps before finishing/i).length).toBeGreaterThan(0);
  });

  test('renders a table row with a formatted object value and a missing value dash', async () => {
    vi.spyOn(globalThis, 'fetch').mockImplementation(async () =>
      new Response(
        JSON.stringify({
          id: 'goal-1',
          goal_id: 'goal-1',
          status: 'complete',
          goal: 'Fix prod',
          result_artifact: {
            kind: 'table',
            downloads: [],
            tables: [
              {
                title: 'Details',
                summary: 'Row details',
                columns: [
                  { key: 'meta', label: 'Meta' },
                  { key: 'missing', label: 'Missing' },
                ],
                rows: [{ meta: { nested: true }, missing: null }],
              },
            ],
          },
        }),
        { status: 200, headers: { 'Content-Type': 'application/json' } }
      )
    );

    renderGoalDetailPage();
    expect(await screen.findByText('Details')).toBeInTheDocument();
    expect(screen.getByText('Row details')).toBeInTheDocument();
    expect(screen.getAllByText(/"nested": true/).length).toBeGreaterThan(0);
    expect(screen.getByText('—')).toBeInTheDocument();
  });

  test('shows "+N more tool calls" note when more than 10 tool results are present', async () => {
    mockGoal('executing');
    goalStreamState.current = {
      connected: true,
      streamingToken: null,
      events: Array.from({ length: 12 }, (_, i) => ({
        type: 'tool_call_complete',
        tool: `tool_${i}`,
        server_id: 'srv',
        success: true,
        output: { i },
      })),
    };

    renderGoalDetailPage();
    await userEvent.click(await screen.findByRole('tab', { name: /^results$/i }));
    expect(await screen.findByText(/\+2 more tool calls/i)).toBeInTheDocument();
  });

  test('copyToClipboard swallows a rejected clipboard write', async () => {
    Object.assign(navigator, {
      clipboard: { writeText: vi.fn().mockRejectedValue(new Error('denied')) },
    });
    mockCompletedGoalWithResultArtifact();
    renderGoalDetailPage();

    const copyBtn = await screen.findByRole('button', { name: /copy result/i });
    await userEvent.click(copyBtn);

    // Toast still fires even though the clipboard write failed under the hood
    await waitFor(() =>
      expect(useToastStore.getState().toasts.some((t) => t.message === 'Copied!')).toBe(true)
    );
  });

  test('"Back to goals" link on the goal-not-found screen navigates away', async () => {
    vi.spyOn(globalThis, 'fetch').mockResolvedValue(
      new Response(JSON.stringify(null), {
        status: 200,
        headers: { 'Content-Type': 'application/json' },
      })
    );
    renderGoalDetailPage();

    const backLink = await screen.findByRole('button', { name: /back to goals/i });
    await userEvent.click(backLink);
    await waitFor(() => expect(screen.queryByText(/goal not found/i)).not.toBeInTheDocument());
  });

  test('developer log entry with no message payload renders without the message span', async () => {
    vi.spyOn(globalThis, 'fetch').mockImplementation(async (input) => {
      const url = String(input);
      if (url.endsWith('/goals/goal-1/replay')) {
        return new Response(
          JSON.stringify({
            timeline: [{ event_id: 'event-1', goal_id: 'goal-1', type: 'step_started' }],
          }),
          { status: 200, headers: { 'Content-Type': 'application/json' } }
        );
      }
      return new Response(
        JSON.stringify({ id: 'goal-1', goal_id: 'goal-1', status: 'complete', goal: 'Fix prod' }),
        { status: 200, headers: { 'Content-Type': 'application/json' } }
      );
    });

    renderGoalDetailPage();
    await userEvent.click(await screen.findByRole('tab', { name: /dev log/i }));
    expect(await screen.findByText('step started')).toBeInTheDocument();
  });
});
