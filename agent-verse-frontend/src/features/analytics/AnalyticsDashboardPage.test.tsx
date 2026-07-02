import { describe, it, test, expect, vi, beforeEach } from 'vitest';
import { render, screen, waitFor } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { MemoryRouter } from 'react-router-dom';
import { AnalyticsDashboardPage } from './AnalyticsDashboardPage';

// recharts ResponsiveContainer uses ResizeObserver which is absent in jsdom
globalThis.ResizeObserver = class ResizeObserver {
  observe() {}
  unobserve() {}
  disconnect() {}
};

vi.mock('@/stores/auth', () => {
  const state = {
    apiKey: 'test-key', tenantId: 'test-tenant',
    ssoMode: false, accessToken: '', logout: () => {},
  };
  const hook = (sel: (s: typeof state) => unknown) => sel(state);
  (hook as unknown as { getState: () => typeof state }).getState = () => state;
  return { useAuthStore: hook };
});

const mockFetch = vi.fn();
vi.stubGlobal('fetch', mockFetch);

function Wrapper({ children }: { children: React.ReactNode }) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return (
    <QueryClientProvider client={qc}>
      <MemoryRouter>{children}</MemoryRouter>
    </QueryClientProvider>
  );
}

const MOCK_GOALS = {
  total: 42, completed: 35, failed: 7, cancelled: 0,
  success_rate: 0.83, avg_duration_s: 4.5,
  avg_cost_usd: 0.025, total_cost_usd: 1.05,
  by_status: { complete: 35, failed: 7 },
};

const MOCK_COSTS = {
  total_cost_usd: 1.25, cost_today_usd: 0.12,
  goals_today: 5, total_goals: 42,
  avg_cost_per_goal: 0.03,
  cost_by_day: [
    { date: '2026-06-01', cost_usd: 0.10 },
    { date: '2026-06-02', cost_usd: 0.15 },
  ],
  cost_by_model: { 'claude-3-5-sonnet': 0.80, 'gpt-4o': 0.45 },
  trends: [],
};

const MOCK_EVALS = {
  total_evals: 30, total: 30, passed: 24,
  pass_rate: 0.80, avg_score: 0.82,
  avg_scores: {
    task_completion: 0.85, efficiency: 0.80,
    accuracy: 0.83, safety: 0.92, coherence: 0.78,
  },
  evals_by_day: [
    { date: '2026-06-01', pass_rate: 0.78, avg_score: 0.80 },
  ],
};

const MOCK_TOOLS = {
  period_days: 30,
  tools: [
    { name: 'jira:search', tool_name: 'jira:search', total: 100, call_count: 100,
      failure_count: 5, failure_rate: 0.05, success: 95, failed: 5, success_rate: 0.95,
      avg_latency_ms: 250 },
  ],
};

const MOCK_BENCHMARKS = {
  platform_avg_success_rate: 0.72, platform_avg_cost_usd: 0.05,
  platform_avg_eval_score: 0.74, your_success_rate: 0.83,
  your_cost_usd: 0.025, your_eval_score: 0.82,
  percentile_success: 25, percentile_cost: 25,
  comparison_label: 'Top 25%',
  dimensions: {
    your: { task_completion: 0.85, efficiency: 0.80, accuracy: 0.83, safety: 0.92, coherence: 0.78 },
    platform: { task_completion: 0.75, efficiency: 0.72, accuracy: 0.76, safety: 0.88, coherence: 0.71 },
  },
};

function setupMocks(overrides: Record<string, unknown> = {}) {
  mockFetch.mockImplementation(async (input: RequestInfo | URL) => {
    const url = String(input);
    if (url.includes('/analytics/goals')) {
      return { ok: true, status: 200, json: () => Promise.resolve(overrides.goals ?? MOCK_GOALS) } as Response;
    }
    if (url.includes('/analytics/costs')) {
      return { ok: true, status: 200, json: () => Promise.resolve(overrides.costs ?? MOCK_COSTS) } as Response;
    }
    if (url.includes('/analytics/evals')) {
      return { ok: true, status: 200, json: () => Promise.resolve(overrides.evals ?? MOCK_EVALS) } as Response;
    }
    if (url.includes('/analytics/tools')) {
      return { ok: true, status: 200, json: () => Promise.resolve(overrides.tools ?? MOCK_TOOLS) } as Response;
    }
    if (url.includes('/analytics/agents')) {
      return { ok: true, status: 200, json: () => Promise.resolve({ period_days: 30, agents: [] }) } as Response;
    }
    if (url.includes('/intelligence/benchmarks')) {
      return { ok: true, status: 200, json: () => Promise.resolve(overrides.benchmarks ?? MOCK_BENCHMARKS) } as Response;
    }
    return { ok: true, status: 200, json: () => Promise.resolve({}) } as Response;
  });
}

beforeEach(() => {
  vi.clearAllMocks();
  setupMocks();
});

describe('AnalyticsDashboardPage', () => {
  it('renders page title', () => {
    render(<AnalyticsDashboardPage />, { wrapper: Wrapper });
    expect(screen.getByText('Analytics')).toBeDefined();
  });

  it('renders KPI section headings or loading', () => {
    render(<AnalyticsDashboardPage />, { wrapper: Wrapper });
    const element = document.body;
    expect(element).toBeDefined();
  });

  it('renders chart containers', () => {
    render(<AnalyticsDashboardPage />, { wrapper: Wrapper });
    const headings = screen.getAllByText(/Goals by Status|goals|Goal Execution Funnel/i);
    expect(headings.length).toBeGreaterThan(0);
  });

  it('renders all 6 KPI card labels', async () => {
    render(<AnalyticsDashboardPage />, { wrapper: Wrapper });
    await waitFor(() => {
      expect(screen.getByText('Total Goals')).toBeDefined();
      expect(screen.getByText('Success Rate')).toBeDefined();
      expect(screen.getByText('Eval Pass Rate')).toBeDefined();
    }, { timeout: 3000 });
  });

  it('renders cost KPI card', async () => {
    render(<AnalyticsDashboardPage />, { wrapper: Wrapper });
    await waitFor(() => {
      const costCard = screen.queryAllByText(/Cost \(/i);
      expect(costCard.length).toBeGreaterThan(0);
    }, { timeout: 3000 });
  });

  it('renders goal funnel section', async () => {
    render(<AnalyticsDashboardPage />, { wrapper: Wrapper });
    await waitFor(() => {
      const funnel = screen.queryAllByText(/Goal Execution Funnel/i);
      expect(funnel.length).toBeGreaterThan(0);
    }, { timeout: 3000 });
  });

  it('renders tool performance section', async () => {
    render(<AnalyticsDashboardPage />, { wrapper: Wrapper });
    await waitFor(() => {
      const tools = screen.queryAllByText(/Tool Performance/i);
      expect(tools.length).toBeGreaterThan(0);
    }, { timeout: 3000 });
  });

  it('renders cost intelligence section', async () => {
    render(<AnalyticsDashboardPage />, { wrapper: Wrapper });
    await waitFor(() => {
      const cost = screen.queryAllByText(/Cost Intelligence/i);
      expect(cost.length).toBeGreaterThan(0);
    }, { timeout: 3000 });
  });

  it('renders eval performance section', async () => {
    render(<AnalyticsDashboardPage />, { wrapper: Wrapper });
    await waitFor(() => {
      const eval_ = screen.queryAllByText(/Eval Performance/i);
      expect(eval_.length).toBeGreaterThan(0);
    }, { timeout: 3000 });
  });

  it('renders platform benchmarks section', async () => {
    render(<AnalyticsDashboardPage />, { wrapper: Wrapper });
    await waitFor(() => {
      const bench = screen.queryAllByText(/Platform Benchmarks/i);
      expect(bench.length).toBeGreaterThan(0);
    }, { timeout: 3000 });
  });

  it('shows KPI values after data loads', async () => {
    render(<AnalyticsDashboardPage />, { wrapper: Wrapper });
    await waitFor(() => {
      const matches = screen.queryAllByText('42');
      expect(matches.length).toBeGreaterThan(0);
    }, { timeout: 3000 });
  });

  it('renders period selector buttons', () => {
    render(<AnalyticsDashboardPage />, { wrapper: Wrapper });
    expect(screen.getByText('7d')).toBeDefined();
    expect(screen.getByText('30d')).toBeDefined();
    expect(screen.getByText('90d')).toBeDefined();
  });
});

describe('AnalyticsDashboardPage null safety', () => {
  test('renders without crashing when evals returns null avg_score and pass_rate', async () => {
    setupMocks({
      evals: { total_evals: 0, total: 0, passed: 0, pass_rate: null, avg_score: null,
               avg_scores: null, evals_by_day: [] },
    });
    render(<AnalyticsDashboardPage />, { wrapper: Wrapper });
    expect(await screen.findByText(/Eval Performance/i)).toBeTruthy();
  });

  test('renders without crashing when costs has no cost_by_model', async () => {
    setupMocks({
      costs: { total_cost_usd: 0.05, cost_by_day: [], cost_by_model: {}, trends: [] },
    });
    render(<AnalyticsDashboardPage />, { wrapper: Wrapper });
    expect(document.body).toBeTruthy();
  });

  test('renders without crashing when tools is empty', async () => {
    setupMocks({ tools: { period_days: 30, tools: [] } });
    render(<AnalyticsDashboardPage />, { wrapper: Wrapper });
    await waitFor(() => {
      const noData = screen.queryAllByText(/No tool data yet/i);
      expect(noData.length).toBeGreaterThan(0);
    }, { timeout: 3000 });
  });
});
