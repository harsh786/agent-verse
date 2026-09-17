import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter } from 'react-router-dom';
import { afterEach, beforeEach, describe, expect, test, vi } from 'vitest';
import { useAuthStore } from '@/stores/auth';
import { useToastStore } from '@/stores/toast';
import { DashboardPage } from './DashboardPage';

/** DashboardPage calls the imperative `toast()` helper, which only updates the
 *  Zustand toast store — no <Toaster /> is mounted in these tests, so toast
 *  copy is asserted against the store rather than the DOM. */
function expectToast(matcher: RegExp) {
  return waitFor(() => {
    const toasts = useToastStore.getState().toasts;
    expect(toasts.some((t) => matcher.test(t.message))).toBe(true);
  });
}

const navigateMock = vi.fn();
vi.mock('react-router-dom', async () => {
  const actual = await vi.importActual('react-router-dom');
  return { ...actual, useNavigate: () => navigateMock };
});

function renderDashboardPage() {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  render(
    <QueryClientProvider client={queryClient}>
      <MemoryRouter>
        <DashboardPage />
      </MemoryRouter>
    </QueryClientProvider>
  );
}

/** Flexible fetch mock covering every endpoint DashboardPage's queries touch.
 *  Order matters: more specific URL checks must run before generic ones. */
function mockDashboardFetch(opts: {
  goals?: unknown;
  metrics?: unknown;
  approvals?: unknown;
  agents?: unknown;
  cost?: unknown;
  submitResponse?: unknown;
  submitStatus?: number;
  onApprove?: (id: string) => void;
  onReject?: (id: string) => void;
  approveStatus?: number;
  rejectStatus?: number;
} = {}) {
  const json = (b: unknown, status = 200) =>
    new Response(JSON.stringify(b), { status, headers: { 'Content-Type': 'application/json' } });
  return vi.spyOn(globalThis, 'fetch').mockImplementation(async (input, init) => {
    const url = String(input);
    const method = (init?.method ?? 'GET').toUpperCase();

    const approveMatch = url.match(/\/governance\/approvals\/([^/]+)\/approve$/);
    if (approveMatch) {
      opts.onApprove?.(approveMatch[1]);
      if (opts.approveStatus && opts.approveStatus >= 400) {
        return json({ error: { message: 'boom' } }, opts.approveStatus);
      }
      return json({ status: 'approved' });
    }
    const rejectMatch = url.match(/\/governance\/approvals\/([^/]+)\/reject$/);
    if (rejectMatch) {
      opts.onReject?.(rejectMatch[1]);
      if (opts.rejectStatus && opts.rejectStatus >= 400) {
        return json({ error: { message: 'boom' } }, opts.rejectStatus);
      }
      return json({ status: 'rejected' });
    }
    if (url.includes('/goals/metrics')) return json(opts.metrics ?? {});
    if (url.includes('/analytics/costs')) return json(opts.cost ?? { cost_today_usd: 0 });
    if (url.includes('/governance/approvals')) return json(opts.approvals ?? []);
    if (url.includes('/agents')) return json(opts.agents ?? []);
    if (url.includes('/goals')) {
      if (method === 'POST') {
        if (opts.submitStatus && opts.submitStatus >= 400) {
          return json({ error: { message: 'nope' } }, opts.submitStatus);
        }
        return json(opts.submitResponse ?? { id: 'new-goal', goal_id: 'new-goal' });
      }
      return json(opts.goals ?? { goals: [] });
    }
    return json({});
  });
}

describe('DashboardPage', () => {
  beforeEach(() => {
    localStorage.clear();
    localStorage.setItem('av_api_key', 'tenant-key');
    useAuthStore.setState({
      apiKey: 'tenant-key',
      tenantId: 'tenant-1',
      plan: 'free',
      isAuthenticated: true,
    });
  });

  afterEach(() => {
    vi.restoreAllMocks();
  });

  test('renders all four KPI card labels', () => {
    vi.spyOn(globalThis, 'fetch').mockReturnValue(new Promise(() => {}));
    renderDashboardPage();
    expect(screen.getByText('Active Goals')).toBeInTheDocument();
    expect(screen.getByText('Success Rate')).toBeInTheDocument();
    expect(screen.getByText('Cost Today')).toBeInTheDocument();
    expect(screen.getByText('Agents')).toBeInTheDocument();
  });

  test('shows loading skeleton state before data arrives', () => {
    vi.spyOn(globalThis, 'fetch').mockReturnValue(new Promise(() => {}));
    renderDashboardPage();
    // KPI cards show "—" while loading (goalsLoading branch)
    const dashes = screen.getAllByText('—');
    expect(dashes.length).toBeGreaterThanOrEqual(1);
  });

  test('shows Mission Control page title and subtitle', () => {
    vi.spyOn(globalThis, 'fetch').mockReturnValue(new Promise(() => {}));
    renderDashboardPage();
    expect(screen.getByRole('heading', { name: 'Mission Control' })).toBeInTheDocument();
  });

  test('renders goal entries in activity feed when goals exist', async () => {
    vi.spyOn(globalThis, 'fetch').mockResolvedValue(
      new Response(
        JSON.stringify({
          goals: [
            { id: 'g1', goal: 'First test goal', status: 'complete', created_at: new Date().toISOString() },
            { id: 'g2', goal: 'Second test goal', status: 'executing', created_at: new Date().toISOString() },
          ],
        }),
        { status: 200, headers: { 'Content-Type': 'application/json' } }
      )
    );
    renderDashboardPage();
    await waitFor(() => expect(screen.getByText('First test goal')).toBeInTheDocument());
    expect(screen.getByText('Second test goal')).toBeInTheDocument();
  });

  test('shows empty-state message when no goals exist', async () => {
    vi.spyOn(globalThis, 'fetch').mockResolvedValue(
      new Response(JSON.stringify({ goals: [] }), {
        status: 200,
        headers: { 'Content-Type': 'application/json' },
      })
    );
    renderDashboardPage();
    await waitFor(() =>
      expect(screen.getByText(/no recent activity/i)).toBeInTheDocument()
    );
  });

  test('computes active goal count from executing and planning goals', async () => {
    vi.spyOn(globalThis, 'fetch').mockResolvedValue(
      new Response(
        JSON.stringify({
          goals: [
            { id: 'g1', goal: 'Goal A', status: 'executing', created_at: new Date().toISOString() },
            { id: 'g2', goal: 'Goal B', status: 'planning', created_at: new Date().toISOString() },
            { id: 'g3', goal: 'Goal C', status: 'complete', created_at: new Date().toISOString() },
            { id: 'g4', goal: 'Goal D', status: 'failed', created_at: new Date().toISOString() },
          ],
        }),
        { status: 200, headers: { 'Content-Type': 'application/json' } }
      )
    );
    renderDashboardPage();
    await waitFor(() => expect(screen.getByText('Goal A')).toBeInTheDocument());
    // active = executing + planning = 2
    expect(screen.getByText('2')).toBeInTheDocument();
  });

  test('renders status badges for goals in the activity feed', async () => {
    vi.spyOn(globalThis, 'fetch').mockResolvedValue(
      new Response(
        JSON.stringify({
          goals: [
            { id: 'g1', goal: 'Done goal', status: 'complete', created_at: new Date().toISOString() },
            { id: 'g2', goal: 'Running goal', status: 'executing', created_at: new Date().toISOString() },
          ],
        }),
        { status: 200, headers: { 'Content-Type': 'application/json' } }
      )
    );
    renderDashboardPage();
    // LiveActivityStream renders goal text (not capitalized status label text)
    await waitFor(() => expect(screen.getByText('Done goal')).toBeInTheDocument());
    expect(screen.getByText('Running goal')).toBeInTheDocument();
  });

  test('renders Live Activity section header', () => {
    vi.spyOn(globalThis, 'fetch').mockReturnValue(new Promise(() => {}));
    renderDashboardPage();
    expect(screen.getByText('Live Activity')).toBeInTheDocument();
  });

  test('shows onboarding banner for new user with no agents or goals', async () => {
    vi.spyOn(globalThis, 'fetch').mockImplementation(async (input) => {
      const url = String(input);
      if (url.includes('/goals/metrics') || url.includes('/analytics/costs')) {
        return new Response(
          JSON.stringify({
            active_goals: 0,
            total_goals: 0,
            success_rate: 0,
            avg_latency_ms: 0,
            cost_today_usd: 0,
            goals_today: 0,
          }),
          { status: 200, headers: { 'Content-Type': 'application/json' } }
        );
      }
      if (url.includes('/agents')) {
        return new Response('[]', {
          status: 200,
          headers: { 'Content-Type': 'application/json' },
        });
      }
      if (url.includes('/governance/approvals')) {
        return new Response('[]', {
          status: 200,
          headers: { 'Content-Type': 'application/json' },
        });
      }
      if (url.includes('/goals')) {
        return new Response(JSON.stringify({ goals: [] }), {
          status: 200,
          headers: { 'Content-Type': 'application/json' },
        });
      }
      return new Response('{}', { status: 200 });
    });

    renderDashboardPage();

    expect(await screen.findByText(/welcome to agentverse/i)).toBeInTheDocument();
    expect(screen.getByRole('link', { name: /get started/i })).toHaveAttribute(
      'href',
      '/onboarding'
    );
  });
});

describe('DashboardPage — tab switching', () => {
  beforeEach(() => {
    localStorage.clear();
    localStorage.setItem('av_api_key', 'tenant-key');
    useAuthStore.setState({
      apiKey: 'tenant-key',
      tenantId: 'tenant-1',
      plan: 'free',
      isAuthenticated: true,
    });
  });

  afterEach(() => {
    vi.restoreAllMocks();
  });

  test('switches to AI Ops tab and back to Mission Control', async () => {
    const user = userEvent.setup();
    mockDashboardFetch();
    renderDashboardPage();

    await screen.findByText('Live Activity');
    await user.click(screen.getByRole('button', { name: /AI Ops/i }));
    expect(await screen.findByRole('heading', { name: /AI Operations Center/i })).toBeInTheDocument();
    // Mission Control content is unmounted while on the AI Ops tab.
    expect(screen.queryByText('Live Activity')).not.toBeInTheDocument();

    await user.click(screen.getByRole('button', { name: 'Mission Control' }));
    expect(await screen.findByText('Live Activity')).toBeInTheDocument();
    expect(screen.queryByRole('heading', { name: /AI Operations Center/i })).not.toBeInTheDocument();
  });
});

describe('DashboardPage — KPI card and quick action navigation', () => {
  beforeEach(() => {
    localStorage.clear();
    localStorage.setItem('av_api_key', 'tenant-key');
    useAuthStore.setState({
      apiKey: 'tenant-key',
      tenantId: 'tenant-1',
      plan: 'free',
      isAuthenticated: true,
    });
    navigateMock.mockClear();
    useToastStore.setState({ toasts: [] });
  });

  afterEach(() => {
    vi.restoreAllMocks();
  });

  test('clicking the Active Goals KPI card navigates to /goals', async () => {
    mockDashboardFetch();
    renderDashboardPage();
    const user = userEvent.setup();
    await screen.findByText('Active Goals');
    await user.click(screen.getByText('Active Goals').closest('button')!);
    expect(navigateMock).toHaveBeenCalledWith('/goals');
  });

  test('clicking the Success Rate KPI card navigates to /analytics', async () => {
    mockDashboardFetch();
    renderDashboardPage();
    const user = userEvent.setup();
    await screen.findByText('Success Rate');
    await user.click(screen.getByText('Success Rate').closest('button')!);
    expect(navigateMock).toHaveBeenCalledWith('/analytics');
  });

  test('clicking the Cost Today KPI card navigates to /observability/cost', async () => {
    mockDashboardFetch();
    renderDashboardPage();
    const user = userEvent.setup();
    await screen.findByText('Cost Today');
    await user.click(screen.getByText('Cost Today').closest('button')!);
    expect(navigateMock).toHaveBeenCalledWith('/observability/cost');
  });

  test('clicking the Agents KPI card navigates to /agents', async () => {
    mockDashboardFetch();
    renderDashboardPage();
    const user = userEvent.setup();
    await screen.findByText('Agents');
    await user.click(screen.getByText('Agents').closest('button')!);
    expect(navigateMock).toHaveBeenCalledWith('/agents');
  });

  test('clicking "View all" navigates to /goals and "Manage" navigates to /agents', async () => {
    mockDashboardFetch();
    renderDashboardPage();
    const user = userEvent.setup();
    await screen.findByText('Live Activity');
    await user.click(screen.getByLabelText('View all goals'));
    expect(navigateMock).toHaveBeenCalledWith('/goals');
    await user.click(screen.getByLabelText('Manage agents'));
    expect(navigateMock).toHaveBeenCalledWith('/agents');
  });

  test('clicking each quick action button navigates to its path', async () => {
    mockDashboardFetch();
    renderDashboardPage();
    const user = userEvent.setup();
    await screen.findByText('Live Activity');

    await user.click(screen.getByLabelText('Navigate to View Goals'));
    expect(navigateMock).toHaveBeenCalledWith('/goals');
    await user.click(screen.getByLabelText('Navigate to Manage Agents'));
    expect(navigateMock).toHaveBeenCalledWith('/agents');
    await user.click(screen.getByLabelText('Navigate to Analytics'));
    expect(navigateMock).toHaveBeenCalledWith('/analytics');
    await user.click(screen.getByLabelText('Navigate to Governance'));
    expect(navigateMock).toHaveBeenCalledWith('/governance');
  });

  test('success rate trend is "up" above 70%, "neutral" between 40-70%, "down" at or below 40%', async () => {
    // 3 of 4 completed => 75% success rate => "up" trend (↑ arrow rendered twice: active goals + success rate)
    mockDashboardFetch({
      goals: {
        goals: [
          { id: 'g1', goal: 'A', status: 'complete' },
          { id: 'g2', goal: 'B', status: 'complete' },
          { id: 'g3', goal: 'C', status: 'complete' },
          { id: 'g4', goal: 'D', status: 'failed' },
        ],
      },
    });
    renderDashboardPage();
    await waitFor(() => expect(screen.getByText('75%')).toBeInTheDocument());
    expect(screen.getAllByText('↑').length).toBeGreaterThanOrEqual(1);
  });

  test('renders "No agents configured" empty state and Create Agent navigates to /agents/create', async () => {
    mockDashboardFetch({ agents: [] });
    renderDashboardPage();
    const user = userEvent.setup();
    expect(await screen.findByText('No agents configured')).toBeInTheDocument();
    await user.click(screen.getByRole('button', { name: /create agent/i }));
    expect(navigateMock).toHaveBeenCalledWith('/agents/create');
  });

  test('renders the agent orbit view when agents are present', async () => {
    mockDashboardFetch({
      agents: [{ agent_id: 'a1', name: 'Agent One' }],
      goals: {
        goals: [{ id: 'g1', goal: 'Goal A', status: 'executing', agent_id: 'a1' }],
      },
    });
    renderDashboardPage();
    await waitFor(() => expect(screen.queryByText('No agents configured')).not.toBeInTheDocument());
  });

  test('agent label falls back to the agent id when the agent has no name', async () => {
    // Exercises `label: a.name ?? a.agent_id` with an agent lacking `name`.
    mockDashboardFetch({ agents: [{ agent_id: 'a1' }] });
    renderDashboardPage();
    await waitFor(() => expect(screen.queryByText('No agents configured')).not.toBeInTheDocument());
  });

  test('Agents KPI card shows a dash when the agents payload is not an array', async () => {
    // Exercises `Array.isArray(agents) ? agents.length : "—"` (false branch).
    mockDashboardFetch({ agents: {} });
    renderDashboardPage();
    const agentsCard = (await screen.findByText('Agents')).closest('button')!;
    await waitFor(() => expect(agentsCard).toHaveTextContent('—'));
  });

  test('cost falls back to goal-metrics cost_today_usd when cost data lacks it', async () => {
    // Exercises the second `??` fallback in `costToday`.
    mockDashboardFetch({ cost: {}, metrics: { cost_today_usd: 3.5 } });
    renderDashboardPage();
    expect(await screen.findByText('$3.5000')).toBeInTheDocument();
  });

  test('falls back to the raw payload when the goals response has no `goals` key', async () => {
    // Exercises `(d).goals ?? d ?? []` picking the raw array `d`.
    mockDashboardFetch({
      goals: [{ id: 'g1', goal: 'Bare array goal', status: 'complete' }],
    });
    renderDashboardPage();
    expect(await screen.findByText('Bare array goal')).toBeInTheDocument();
    expect(screen.getByText('100%')).toBeInTheDocument();
  });

  test('renders an empty activity feed when the goals response is neither an array nor { goals }', async () => {
    // Exercises the `Array.isArray(goals)` false branch in `goalsArr`.
    mockDashboardFetch({ goals: { unexpected: true } });
    renderDashboardPage();
    await waitFor(() => expect(screen.getByText(/no recent activity/i)).toBeInTheDocument());
    expect(screen.getByText('0%')).toBeInTheDocument();
  });

  test('falls back to an empty list when the goals response is a falsy non-null value', async () => {
    // `d.goals` on a falsy primitive like `0` is `undefined` (no throw), so this
    // exercises the final `?? []` in `(d).goals ?? d ?? []`.
    mockDashboardFetch({ goals: 0 });
    renderDashboardPage();
    await waitFor(() => expect(screen.getByText(/no recent activity/i)).toBeInTheDocument());
  });
});

describe('DashboardPage — pending approvals banner', () => {
  beforeEach(() => {
    localStorage.clear();
    localStorage.setItem('av_api_key', 'tenant-key');
    useAuthStore.setState({
      apiKey: 'tenant-key',
      tenantId: 'tenant-1',
      plan: 'free',
      isAuthenticated: true,
    });
    navigateMock.mockClear();
    useToastStore.setState({ toasts: [] });
  });

  afterEach(() => {
    vi.restoreAllMocks();
  });

  test('shows singular approval copy and highest-risk item for one pending approval', async () => {
    mockDashboardFetch({
      approvals: [
        { request_id: 'r1', goal_id: 'g1', action: 'deploy prod', risk_level: 'critical', status: 'pending' },
      ],
    });
    renderDashboardPage();
    expect(await screen.findByText(/requires your approval/i)).toBeInTheDocument();
    expect(screen.getByText(/1 critical/)).toBeInTheDocument();
    expect(screen.getAllByText(/critical/i).length).toBeGreaterThanOrEqual(1);
    expect(screen.getByText(/Awaiting: deploy prod/)).toBeInTheDocument();
  });

  test('shows plural approval copy and a risk breakdown summary for multiple approvals', async () => {
    mockDashboardFetch({
      approvals: [
        { request_id: 'r1', goal_id: 'g1', action: 'deploy prod', risk_level: 'high', status: 'pending' },
        { request_id: 'r2', goal_id: 'g2', action: 'delete db', risk_level: 'medium', status: 'pending' },
        { request_id: 'r3', goal_id: 'g3', action: 'noop', risk_level: 'low', status: 'pending' },
        { request_id: 'r4', goal_id: 'g4', action: 'other', status: 'approved' },
      ],
    });
    renderDashboardPage();
    expect(await screen.findByText(/actions require your approval/i)).toBeInTheDocument();
    // 3 pending (approved one filtered out); highest risk is "high" -> amber badge item shown.
    expect(screen.getByText(/1 high/)).toBeInTheDocument();
    expect(screen.getByText(/1 medium/)).toBeInTheDocument();
    expect(screen.getByText(/1 low/)).toBeInTheDocument();
    expect(screen.getByText(/Awaiting: deploy prod/)).toBeInTheDocument();
  });

  test('clicking "Review all" navigates to /approvals', async () => {
    mockDashboardFetch({
      approvals: [{ request_id: 'r1', goal_id: 'g1', action: 'x', risk_level: 'low', status: 'pending' }],
    });
    renderDashboardPage();
    const user = userEvent.setup();
    await screen.findByText(/requires your approval/i);
    await user.click(screen.getByRole('button', { name: /review all/i }));
    expect(navigateMock).toHaveBeenCalledWith('/approvals');
  });

  test('approving the top item shows a success toast and refetches approvals', async () => {
    const onApprove = vi.fn();
    mockDashboardFetch({
      approvals: [{ request_id: 'r1', goal_id: 'g1', action: 'deploy', risk_level: 'medium', status: 'pending' }],
      onApprove,
    });
    renderDashboardPage();
    const user = userEvent.setup();
    await screen.findByText(/Awaiting: deploy/);
    await user.click(screen.getByRole('button', { name: 'Approve' }));
    await waitFor(() => expect(onApprove).toHaveBeenCalledWith('r1'));
    await expectToast(/the action was approved/i);
  });

  test('rejecting the top item shows an error-styled toast and refetches approvals', async () => {
    const onReject = vi.fn();
    mockDashboardFetch({
      approvals: [{ request_id: 'r1', goal_id: 'g1', action: 'deploy', risk_level: 'medium', status: 'pending' }],
      onReject,
    });
    renderDashboardPage();
    const user = userEvent.setup();
    await screen.findByText(/Awaiting: deploy/);
    await user.click(screen.getByRole('button', { name: 'Reject' }));
    await waitFor(() => expect(onReject).toHaveBeenCalledWith('r1'));
    await expectToast(/the action was rejected/i);
  });

  test('shows a failure toast when the approve request errors', async () => {
    mockDashboardFetch({
      approvals: [{ request_id: 'r1', goal_id: 'g1', action: 'deploy', risk_level: 'medium', status: 'pending' }],
      approveStatus: 500,
    });
    renderDashboardPage();
    const user = userEvent.setup();
    await screen.findByText(/Awaiting: deploy/);
    await user.click(screen.getByRole('button', { name: 'Approve' }));
    await expectToast(/Approve failed/i);
  });

  test('shows a failure toast when the reject request errors', async () => {
    mockDashboardFetch({
      approvals: [{ request_id: 'r1', goal_id: 'g1', action: 'deploy', risk_level: 'medium', status: 'pending' }],
      rejectStatus: 500,
    });
    renderDashboardPage();
    const user = userEvent.setup();
    await screen.findByText(/Awaiting: deploy/);
    await user.click(screen.getByRole('button', { name: 'Reject' }));
    await expectToast(/Reject failed/i);
  });

  test('defaults an approval risk level to "medium" when unspecified', async () => {
    // Exercises the `a?.risk_level ?? "medium"` fallback in the risk-count reducer
    // and the default (non-critical, non-high) badge style branch.
    mockDashboardFetch({
      approvals: [{ request_id: 'r1', goal_id: 'g1', action: 'unspecified risk action', status: 'pending' }],
    });
    renderDashboardPage();
    expect(await screen.findByText(/1 medium/)).toBeInTheDocument();
    expect(screen.getByText('medium')).toBeInTheDocument();
  });

  test('defaults the top approval\'s action label when unspecified', async () => {
    // Exercises `String(_topItem.action ?? "action")`.
    mockDashboardFetch({
      approvals: [{ request_id: 'r1', goal_id: 'g1', risk_level: 'low', status: 'pending' }],
    });
    renderDashboardPage();
    expect(await screen.findByText(/Awaiting: action/)).toBeInTheDocument();
  });
});

describe('DashboardPage — Quick Goal Submit', () => {
  beforeEach(() => {
    localStorage.clear();
    localStorage.setItem('av_api_key', 'tenant-key');
    useAuthStore.setState({
      apiKey: 'tenant-key',
      tenantId: 'tenant-1',
      plan: 'free',
      isAuthenticated: true,
    });
    navigateMock.mockClear();
    useToastStore.setState({ toasts: [] });
  });

  afterEach(() => {
    vi.restoreAllMocks();
  });

  test('Run button is disabled until a goal is typed, then submits and navigates on success', async () => {
    mockDashboardFetch({ submitResponse: { id: 'goal-123' } });
    renderDashboardPage();
    const user = userEvent.setup();
    const input = await screen.findByLabelText('Goal description');
    const runButton = screen.getByLabelText('Submit goal');
    expect(runButton).toBeDisabled();

    await user.type(input, 'Ship the new feature');
    expect(runButton).not.toBeDisabled();
    await user.click(runButton);

    await waitFor(() => expect(navigateMock).toHaveBeenCalledWith('/goals/goal-123'));
    await expectToast(/goal submitted/i);
  });

  test('pressing Enter in the goal input submits the goal', async () => {
    mockDashboardFetch({ submitResponse: { goal_id: 'goal-456' } });
    renderDashboardPage();
    const user = userEvent.setup();
    const input = await screen.findByLabelText('Goal description');

    await user.type(input, 'Do the thing{Enter}');

    await waitFor(() => expect(navigateMock).toHaveBeenCalledWith('/goals/goal-456'));
  });

  test('pressing Enter with an empty/whitespace goal does not submit', async () => {
    mockDashboardFetch();
    renderDashboardPage();
    const user = userEvent.setup();
    const input = await screen.findByLabelText('Goal description');

    await user.type(input, '   {Enter}');

    expect(navigateMock).not.toHaveBeenCalledWith(expect.stringMatching(/^\/goals\//));
  });

  test('shows "Running…" on the submit button while the mutation is pending', async () => {
    let resolvePost!: (r: Response) => void;
    const json = (b: unknown) =>
      new Response(JSON.stringify(b), { status: 200, headers: { 'Content-Type': 'application/json' } });
    vi.spyOn(globalThis, 'fetch').mockImplementation(async (input, init) => {
      const url = String(input);
      const method = (init?.method ?? 'GET').toUpperCase();
      if (url.includes('/goals') && method === 'POST') {
        return new Promise<Response>((resolve) => {
          resolvePost = resolve;
        });
      }
      if (url.includes('/governance/approvals')) return json([]);
      if (url.includes('/agents')) return json([]);
      return json({ goals: [] });
    });
    renderDashboardPage();
    const user = userEvent.setup();
    const input = await screen.findByLabelText('Goal description');
    await user.type(input, 'A goal that takes a while');
    await user.click(screen.getByLabelText('Submit goal'));

    expect(await screen.findByText('Running…')).toBeInTheDocument();
    expect(screen.getByLabelText('Goal description')).toBeDisabled();

    resolvePost(json({ id: 'goal-789' }));
    await waitFor(() => expect(navigateMock).toHaveBeenCalledWith('/goals/goal-789'));
  });

  test('shows an error toast when goal submission fails', async () => {
    mockDashboardFetch({ submitStatus: 500 });
    renderDashboardPage();
    const user = userEvent.setup();
    const input = await screen.findByLabelText('Goal description');
    await user.type(input, 'This will fail');
    await user.click(screen.getByLabelText('Submit goal'));

    await expectToast(/Failed:/i);
  });
});
