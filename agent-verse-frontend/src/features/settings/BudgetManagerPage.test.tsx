import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { render, screen, waitFor, fireEvent } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { afterEach, beforeEach, describe, expect, test, vi } from 'vitest';
import { useAuthStore } from '@/stores/auth';
import { BudgetManagerPage } from './BudgetManagerPage';

// ── Test utilities ─────────────────────────────────────────────────────────────

function renderPage() {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={qc}>
      <MemoryRouter>
        <BudgetManagerPage />
      </MemoryRouter>
    </QueryClientProvider>
  );
}

// ── Mock data ─────────────────────────────────────────────────────────────────

const MOCK_SUMMARY = {
  total_cost_usd: 25.5,
  cost_by_day: [
    { date: '2024-01-01', cost_usd: 3.0 },
    { date: '2024-01-02', cost_usd: 5.0 },
    { date: '2024-01-03', cost_usd: 4.5 },
    { date: '2024-01-04', cost_usd: 6.0 },
    { date: '2024-01-05', cost_usd: 7.0 },
  ],
  cost_by_model: { 'claude-3-sonnet': 15.5, 'gpt-4': 10.0 },
  daily_budget_usd: 50.0,
  budget_utilization: 0.51,
};

const MOCK_BUDGETS = {
  daily_limit: 50.0,
  per_goal_usd: 5.0,
  per_tenant_daily_usd: 50.0,
  budget_pct_remaining: 49.0,
  daily_spent: 25.5,
};

const MOCK_GOV_BUDGET = {
  tenant_id: 'tenant-1',
  per_goal_usd: 5.0,
  per_tenant_daily_usd: 50.0,
};

const MOCK_PER_AGENT = [
  {
    agent_id: 'a1',
    agent_name: 'Triage Bot',
    total_cost_usd: 12.5,
    goal_count: 10,
    avg_cost_per_goal: 1.25,
  },
  {
    agent_id: 'a2',
    agent_name: 'Researcher',
    total_cost_usd: 8.0,
    goal_count: 5,
    avg_cost_per_goal: 1.6,
  },
];

const MOCK_ANOMALIES = [
  {
    id: 'an1',
    anomaly_type: 'cost_spike',
    cost_actual_usd: 10.0,
    cost_baseline_usd: 2.0,
    sigma_deviation: 3.5,
    detected_at: new Date(Date.now() - 3600_000).toISOString(),
    severity: 'high' as const,
    cost_delta_usd: 8.0,
    message: 'Unusual cost spike detected for agent a1',
  },
];

// ── Mock helpers ──────────────────────────────────────────────────────────────

function mockFetch(overrides?: {
  summary?: object;
  budgets?: object;
  govBudget?: object;
  perAgent?: object[];
  anomalies?: object[];
}) {
  return vi.spyOn(globalThis, 'fetch').mockImplementation(async (input, init) => {
    const url = String(input);
    const method = (init as RequestInit | undefined)?.method ?? 'GET';

    if (url.includes('/costs/summary')) {
      return new Response(JSON.stringify(overrides?.summary ?? MOCK_SUMMARY), {
        status: 200,
        headers: { 'Content-Type': 'application/json' },
      });
    }
    if (url.includes('/costs/budgets') && method !== 'PUT') {
      return new Response(JSON.stringify(overrides?.budgets ?? MOCK_BUDGETS), {
        status: 200,
        headers: { 'Content-Type': 'application/json' },
      });
    }
    if (url.includes('/costs/per-agent')) {
      return new Response(JSON.stringify(overrides?.perAgent ?? MOCK_PER_AGENT), {
        status: 200,
        headers: { 'Content-Type': 'application/json' },
      });
    }
    if (url.includes('/costs/anomalies')) {
      return new Response(JSON.stringify(overrides?.anomalies ?? []), {
        status: 200,
        headers: { 'Content-Type': 'application/json' },
      });
    }
    if (url.includes('/governance/budget')) {
      return new Response(JSON.stringify(overrides?.govBudget ?? MOCK_GOV_BUDGET), {
        status: 200,
        headers: { 'Content-Type': 'application/json' },
      });
    }
    // PUT /costs/budgets or /governance/budget
    if (method === 'PUT') {
      return new Response(null, { status: 204 });
    }
    return new Response(null, { status: 404 });
  });
}

// ── Tests ─────────────────────────────────────────────────────────────────────

describe('BudgetManagerPage', () => {
  beforeEach(() => {
    useAuthStore.setState({
      apiKey: 'test-key',
      tenantId: 'tenant-1',
      plan: 'professional',
      isAuthenticated: true,
    });
  });

  afterEach(() => {
    vi.restoreAllMocks();
  });

  test('renders Budget Manager heading', async () => {
    mockFetch();
    renderPage();
    await waitFor(() =>
      expect(screen.getByText('Budget Manager')).toBeInTheDocument()
    );
  });

  test('shows loading skeletons while data is in-flight', () => {
    vi.spyOn(globalThis, 'fetch').mockReturnValue(new Promise(() => {}));
    renderPage();
    // Heading is always shown; multiple skeleton elements should be rendered
    expect(screen.getByText('Budget Manager')).toBeInTheDocument();
    // Skeletons render while loading — aria-hidden pulse elements exist
    const pulseEls = document.querySelectorAll('.animate-pulse');
    expect(pulseEls.length).toBeGreaterThan(0);
  });

  test('shows daily spend stat card with value from budgets', async () => {
    mockFetch({ budgets: { ...MOCK_BUDGETS, daily_spent: 25.5 } });
    renderPage();
    // $25.50 may appear in multiple places (stat card + chart subtitle) — use getAllByText
    await waitFor(() => {
      const matches = screen.getAllByText('$25.50');
      expect(matches.length).toBeGreaterThan(0);
    });
    // Specifically verify the "Daily Spend" label is present
    await waitFor(() => expect(screen.getByText('Daily Spend')).toBeInTheDocument());
  });

  test('shows daily budget in stat card from gov budget', async () => {
    mockFetch({ govBudget: { ...MOCK_GOV_BUDGET, per_tenant_daily_usd: 50.0 } });
    renderPage();
    // Daily Budget stat card should show the $50.00 limit
    await waitFor(() => {
      const matches = screen.getAllByText('$50.00');
      expect(matches.length).toBeGreaterThan(0);
    });
  });

  test('shows agent names in the per-agent breakdown table', async () => {
    mockFetch();
    renderPage();
    // Names appear in both the overrides panel and the breakdown table — use getAllByText
    await waitFor(
      () => expect(screen.getAllByText('Triage Bot').length).toBeGreaterThan(0),
      { timeout: 4000 }
    );
    await waitFor(() => {
      const matches = screen.getAllByText('Researcher');
      expect(matches.length).toBeGreaterThan(0);
    });
  });

  test('shows error state with Retry button when both budget queries fail', async () => {
    vi.spyOn(globalThis, 'fetch').mockResolvedValue(
      new Response('{"error":{"message":"Internal Server Error"}}', { status: 500 })
    );
    renderPage();
    await waitFor(() =>
      expect(screen.getByText(/failed to load budget/i)).toBeInTheDocument()
    );
    expect(screen.getByRole('button', { name: /retry/i })).toBeInTheDocument();
  });

  test('shows no-anomalies green state when anomaly list is empty', async () => {
    mockFetch({ anomalies: [] });
    renderPage();
    await waitFor(() =>
      expect(screen.getByText(/no anomalies detected/i)).toBeInTheDocument()
    );
    expect(screen.getByText(/all costs within normal range/i)).toBeInTheDocument();
  });

  test('shows anomaly count badge and anomaly data when anomalies present', async () => {
    mockFetch({ anomalies: MOCK_ANOMALIES });
    renderPage();
    await waitFor(() =>
      expect(screen.getByText('cost_spike')).toBeInTheDocument()
    );
    // Count badge should show 1
    await waitFor(() => {
      // The anomalies count badge is rendered as "1"
      const countBadge = screen.getByText('1');
      expect(countBadge).toBeInTheDocument();
    });
  });

  test('cost predictor section is collapsed by default', async () => {
    mockFetch();
    renderPage();
    await waitFor(() =>
      expect(screen.getByText('Budget Manager')).toBeInTheDocument()
    );
    // The textarea for goal input should not be visible when collapsed
    expect(screen.queryByPlaceholderText(/describe the goal/i)).not.toBeInTheDocument();
  });

  test('cost predictor expands and shows input when toggled', async () => {
    mockFetch();
    renderPage();
    await waitFor(() =>
      expect(screen.getByText('Cost Predictor')).toBeInTheDocument()
    );
    // The predictor toggle button has aria-expanded and is labelled by its h2 ("Cost Predictor")
    const predictorToggle = screen.getByRole('button', { name: /cost predictor/i });
    expect(predictorToggle).toBeDefined();
    expect(predictorToggle.getAttribute('aria-expanded')).toBe('false');
    fireEvent.click(predictorToggle);
    await waitFor(() =>
      expect(screen.getByPlaceholderText(/describe the goal/i)).toBeInTheDocument()
    );
    expect(predictorToggle.getAttribute('aria-expanded')).toBe('true');
  });

  test('budget config section shows global limits form inputs', async () => {
    mockFetch();
    renderPage();
    await waitFor(() =>
      expect(screen.getByText('Global Limits')).toBeInTheDocument()
    );
    await waitFor(() =>
      expect(screen.getByText('Daily Budget (USD)')).toBeInTheDocument()
    );
    expect(screen.getByText('Per-Goal Budget (USD)')).toBeInTheDocument();
  });

  test('shows model cost breakdown from summary data', async () => {
    mockFetch({ summary: MOCK_SUMMARY });
    renderPage();
    await waitFor(() =>
      expect(screen.getByText('claude-3-sonnet')).toBeInTheDocument()
    );
    expect(screen.getByText('gpt-4')).toBeInTheDocument();
  });
});
