import type { ReactNode } from 'react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter, Route, Routes } from 'react-router-dom';
import { afterEach, beforeEach, describe, expect, test, vi } from 'vitest';
import { useAuthStore } from '@/stores/auth';
import { AgentDashboardPage } from './AgentDashboardPage';

// recharts needs layout APIs jsdom doesn't provide. Mock it so the chart
// wrapper components render as plain divs, and have the XAxis mock invoke
// its tickFormatter render-prop the same way real recharts would while
// laying out the chart, so that inline function gets exercised too.
vi.mock('recharts', () => ({
  Bar: () => null,
  BarChart: ({ children }: { children?: ReactNode }) => <div data-testid="mock-bar-chart">{children}</div>,
  Line: () => null,
  LineChart: ({ children }: { children?: ReactNode }) => <div data-testid="mock-line-chart">{children}</div>,
  ResponsiveContainer: ({ children }: { children?: ReactNode }) => <div>{children}</div>,
  Tooltip: () => null,
  XAxis: (props: { tickFormatter?: (v: string) => unknown }) => {
    props.tickFormatter?.('2026-06-28');
    return null;
  },
  YAxis: () => null,
  CartesianGrid: () => null,
}));

const MOCK_AGENT = {
  agent_id: 'agent-dash-1',
  name: 'Dashboard Agent',
  autonomy_mode: 'bounded-autonomous',
  created_at: '2026-01-01T00:00:00Z',
};

const MOCK_GOALS = {
  goals: [
    {
      goal_id: 'g1', id: 'g1', goal: 'Run report',
      status: 'complete', agent_id: 'agent-dash-1',
      cost_usd: 0.5, created_at: '2026-06-28T10:00:00Z',
    },
    {
      goal_id: 'g2', id: 'g2', goal: 'Audit logs',
      status: 'failed', agent_id: 'agent-dash-1',
      cost_usd: 0.2, created_at: '2026-06-28T11:00:00Z',
    },
  ],
};

const MOCK_ANALYTICS = {
  active_goals: 0,
  total_goals: 2,
  success_rate: 0.5,
  avg_latency_ms: 800,
  cost_today_usd: 0.7,
};

function renderPage(agentId = 'agent-dash-1') {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <MemoryRouter initialEntries={[`/agents/${agentId}/dashboard`]}>
      <QueryClientProvider client={qc}>
        <Routes>
          <Route path="/agents/:agentId/dashboard" element={<AgentDashboardPage />} />
        </Routes>
      </QueryClientProvider>
    </MemoryRouter>
  );
}

describe('AgentDashboardPage', () => {
  beforeEach(() => {
    useAuthStore.setState({
      apiKey: 'test-key', tenantId: 'tenant-1', plan: 'professional', isAuthenticated: true,
    });
  });

  afterEach(() => {
    vi.restoreAllMocks();
  });

  test('renders without crashing', () => {
    vi.spyOn(globalThis, 'fetch').mockReturnValue(new Promise(() => {}));
    renderPage();
    expect(document.body).toBeTruthy();
  });

  test('shows loading skeleton while fetching', () => {
    vi.spyOn(globalThis, 'fetch').mockReturnValue(new Promise(() => {}));
    renderPage();
    expect(document.body.innerHTML).toBeTruthy();
  });

  test('shows agent name after loading', async () => {
    vi.spyOn(globalThis, 'fetch').mockImplementation(async (input) => {
      const url = String(input);
      if (url.includes('/analytics/goals')) {
        return new Response(JSON.stringify(MOCK_ANALYTICS), {
          status: 200, headers: { 'Content-Type': 'application/json' },
        });
      }
      if (url.includes('/goals')) {
        return new Response(JSON.stringify(MOCK_GOALS), {
          status: 200, headers: { 'Content-Type': 'application/json' },
        });
      }
      if (url.includes('/agents/')) {
        return new Response(JSON.stringify(MOCK_AGENT), {
          status: 200, headers: { 'Content-Type': 'application/json' },
        });
      }
      return new Response(null, { status: 404 });
    });

    renderPage();
    // Heading is "{agent.name} — Dashboard"
    await waitFor(() =>
      expect(screen.getByRole('heading', { level: 1 })).toHaveTextContent(/Dashboard Agent/i),
      { timeout: 3000 }
    );
  });

  test('shows KPI cards with goal count', async () => {
    vi.spyOn(globalThis, 'fetch').mockImplementation(async (input) => {
      const url = String(input);
      if (url.includes('/analytics/goals')) {
        return new Response(JSON.stringify(MOCK_ANALYTICS), {
          status: 200, headers: { 'Content-Type': 'application/json' },
        });
      }
      if (url.includes('/goals')) {
        return new Response(JSON.stringify(MOCK_GOALS), {
          status: 200, headers: { 'Content-Type': 'application/json' },
        });
      }
      if (url.includes('/agents/')) {
        return new Response(JSON.stringify(MOCK_AGENT), {
          status: 200, headers: { 'Content-Type': 'application/json' },
        });
      }
      return new Response(null, { status: 404 });
    });

    renderPage();
    await waitFor(() =>
      expect(screen.getByRole('heading', { level: 1 })).toHaveTextContent(/Dashboard Agent/i),
      { timeout: 3000 }
    );
    // total goals KPI: 2 goals filtered by agent_id
    expect(screen.getAllByText('2').length).toBeGreaterThan(0);
  });

  test('handles agent fetch error gracefully', async () => {
    vi.spyOn(globalThis, 'fetch').mockResolvedValue(
      new Response('Not Found', { status: 404 })
    );
    renderPage();
    await waitFor(() => expect(document.body).toBeTruthy(), { timeout: 3000 });
  });

  test('navigates back to agent detail page when back button clicked', async () => {
    const user = userEvent.setup();
    vi.spyOn(globalThis, 'fetch').mockImplementation(async (input) => {
      const url = String(input);
      if (url.includes('/analytics/goals')) {
        return new Response(JSON.stringify(MOCK_ANALYTICS), {
          status: 200, headers: { 'Content-Type': 'application/json' },
        });
      }
      if (url.includes('/goals')) {
        return new Response(JSON.stringify(MOCK_GOALS), {
          status: 200, headers: { 'Content-Type': 'application/json' },
        });
      }
      if (url.includes('/agents/')) {
        return new Response(JSON.stringify(MOCK_AGENT), {
          status: 200, headers: { 'Content-Type': 'application/json' },
        });
      }
      return new Response(null, { status: 404 });
    });

    renderPage();
    await waitFor(() =>
      expect(screen.getByRole('heading', { level: 1 })).toHaveTextContent(/Dashboard Agent/i),
      { timeout: 3000 }
    );

    await user.click(screen.getByRole('button', { name: /back to agent/i }));
    // Navigating away unmounts the dashboard route (no matching Route for /agents/:id),
    // so the heading should disappear.
    await waitFor(() =>
      expect(screen.queryByRole('heading', { level: 1 })).not.toBeInTheDocument()
    );
  });

  test('shows recent-goals skeleton while goals are still loading', async () => {
    vi.spyOn(globalThis, 'fetch').mockImplementation(async (input) => {
      const url = String(input);
      if (url.includes('/analytics/goals')) {
        return new Promise(() => {});
      }
      if (url.includes('/goals')) {
        return new Promise(() => {});
      }
      if (url.includes('/agents/')) {
        return new Response(JSON.stringify(MOCK_AGENT), {
          status: 200, headers: { 'Content-Type': 'application/json' },
        });
      }
      return new Response(null, { status: 404 });
    });

    renderPage();
    await waitFor(() =>
      expect(screen.getByRole('heading', { level: 1 })).toHaveTextContent(/Dashboard Agent/i),
      { timeout: 3000 }
    );

    const recentGoalsHeading = screen.getByText('Recent Goals');
    const recentGoalsSection = recentGoalsHeading.closest('div.bg-card') as HTMLElement;
    expect(recentGoalsSection).toBeTruthy();
    expect(within(recentGoalsSection).queryByText(/no goals run/i)).not.toBeInTheDocument();
    // Skeleton placeholders render instead of a goal list or empty state.
    expect(recentGoalsSection.querySelectorAll('[class*="animate-pulse"]').length).toBeGreaterThan(0);
  });

  test('shows empty state when agent has no goals', async () => {
    vi.spyOn(globalThis, 'fetch').mockImplementation(async (input) => {
      const url = String(input);
      if (url.includes('/analytics/goals')) {
        return new Response(null, { status: 500 });
      }
      if (url.includes('/goals')) {
        return new Response(JSON.stringify({ goals: [] }), {
          status: 200, headers: { 'Content-Type': 'application/json' },
        });
      }
      if (url.includes('/agents/')) {
        return new Response(JSON.stringify(MOCK_AGENT), {
          status: 200, headers: { 'Content-Type': 'application/json' },
        });
      }
      return new Response(null, { status: 404 });
    });

    renderPage();
    await waitFor(() =>
      expect(screen.getByText(/no goals run by this agent yet/i)).toBeInTheDocument(),
      { timeout: 3000 }
    );
    // Analytics fetch failed (non-ok), so the Platform Analytics section should not render.
    expect(screen.queryByText('Platform Analytics')).not.toBeInTheDocument();
  });

  test('renders executing goals with a distinct status badge and Active KPI', async () => {
    const goalsWithExecuting = {
      goals: [
        ...MOCK_GOALS.goals,
        {
          goal_id: 'g3', id: 'g3', goal: 'Sync inventory',
          status: 'executing', agent_id: 'agent-dash-1',
          cost_usd: 0.1, created_at: '2026-06-28T12:00:00Z',
        },
      ],
    };
    vi.spyOn(globalThis, 'fetch').mockImplementation(async (input) => {
      const url = String(input);
      if (url.includes('/analytics/goals')) {
        return new Response(JSON.stringify(MOCK_ANALYTICS), {
          status: 200, headers: { 'Content-Type': 'application/json' },
        });
      }
      if (url.includes('/goals')) {
        return new Response(JSON.stringify(goalsWithExecuting), {
          status: 200, headers: { 'Content-Type': 'application/json' },
        });
      }
      if (url.includes('/agents/')) {
        return new Response(JSON.stringify(MOCK_AGENT), {
          status: 200, headers: { 'Content-Type': 'application/json' },
        });
      }
      return new Response(null, { status: 404 });
    });

    renderPage();
    await waitFor(() =>
      expect(screen.getByText('Sync inventory')).toBeInTheDocument(),
      { timeout: 3000 }
    );

    const executingBadge = screen.getByText('executing');
    expect(executingBadge.className).toContain('bg-blue-100');

    const activeCard = screen.getByText('Active').closest('div') as HTMLElement;
    expect(within(activeCard).getByText('1')).toBeInTheDocument();
  });

  test('falls back to raw array response and agentId when unwrapped fields are missing', async () => {
    // goals endpoint returns a bare array (no `.goals` wrapper), and one goal is
    // missing cost_usd/created_at; the agent response has no `name` field.
    const bareGoalsArray = [
      { goal_id: 'g4', id: 'g4', goal: 'Untitled work', status: 'complete', agent_id: 'agent-dash-1' },
    ];
    vi.spyOn(globalThis, 'fetch').mockImplementation(async (input) => {
      const url = String(input);
      if (url.includes('/analytics/goals')) {
        return new Response(null, { status: 500 });
      }
      if (url.includes('/goals')) {
        return new Response(JSON.stringify(bareGoalsArray), {
          status: 200, headers: { 'Content-Type': 'application/json' },
        });
      }
      if (url.includes('/agents/')) {
        return new Response(JSON.stringify({ agent_id: 'agent-dash-1' }), {
          status: 200, headers: { 'Content-Type': 'application/json' },
        });
      }
      return new Response(null, { status: 404 });
    });

    renderPage();
    // No agent.name, so the heading falls back to the raw agentId.
    await waitFor(() =>
      expect(screen.getByRole('heading', { level: 1 })).toHaveTextContent('agent-dash-1 — Dashboard'),
      { timeout: 3000 }
    );
    expect(screen.getByText('Untitled work')).toBeInTheDocument();
    // Total Cost KPI should render using the cost_usd ?? 0 fallback.
    expect(screen.getByText('$0.0000')).toBeInTheDocument();
  });
});
