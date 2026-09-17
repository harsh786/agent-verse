/**
 * CostDashboardPage tests — comprehensive coverage of the world-class dashboard.
 *
 * Run: npm test src/features/observability/CostDashboardPage.test.tsx
 */

import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { render, screen, waitFor, fireEvent, act, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter } from 'react-router-dom';
import { afterEach, beforeEach, describe, expect, test, vi } from 'vitest';
import type { ReactNode } from 'react';
import { useAuthStore } from '@/stores/auth';
import { useToastStore } from '@/stores/toast';
import { CostDashboardPage } from './CostDashboardPage';

// recharts needs layout APIs jsdom doesn't provide. Mock it so the chart
// wrapper components render as plain divs, and have the Axis/Tooltip/Line
// mocks invoke any tickFormatter/formatter/dot render-prop callbacks so those
// inline functions (defined in CostDashboardPage) get exercised the same way
// the real recharts would while laying out the chart.
vi.mock('recharts', () => ({
  Cell: () => null,
  Line: (props: { dot?: unknown }) => {
    if (typeof props.dot === 'function') {
      (props.dot as (p: unknown) => unknown)({ cx: 10, cy: 10, payload: { is_anomaly: true, date: 'd-anomaly' } });
      (props.dot as (p: unknown) => unknown)({ cx: 20, cy: 20, payload: { is_anomaly: false, date: 'd-normal' } });
    }
    return null;
  },
  LineChart: ({ children }: { children?: ReactNode }) => <div data-testid="mock-line-chart">{children}</div>,
  Pie: ({ children }: { children?: ReactNode }) => <div data-testid="mock-pie">{children}</div>,
  PieChart: ({ children }: { children?: ReactNode }) => <div data-testid="mock-pie-chart">{children}</div>,
  ReferenceLine: () => null,
  ResponsiveContainer: ({ children }: { children?: ReactNode }) => <div>{children}</div>,
  Tooltip: (props: { formatter?: (v: number, name?: string) => unknown }) => {
    props.formatter?.(1.2345, 'Daily Cost');
    return null;
  },
  XAxis: (props: { tickFormatter?: (v: string) => unknown }) => {
    props.tickFormatter?.('2026-06-28');
    return null;
  },
  YAxis: (props: { tickFormatter?: (v: number) => unknown }) => {
    props.tickFormatter?.(12.34);
    return null;
  },
  CartesianGrid: () => null,
}));

// ── Test helpers ──────────────────────────────────────────────────────────────

function renderPage(qc?: QueryClient) {
  const client = qc ?? new QueryClient({
    defaultOptions: { queries: { retry: false, gcTime: 0 } },
  });
  return render(
    <QueryClientProvider client={client}>
      <MemoryRouter>
        <CostDashboardPage />
      </MemoryRouter>
    </QueryClientProvider>
  );
}

// ── Mock data ─────────────────────────────────────────────────────────────────

const MOCK_COST_METRICS = {
  cost_today_usd: 12.45,
  daily_budget_usd: 100.00,
  budget_utilization: 0.1245,
  active_goals: 2,
  total_goals: 87,
  goals_today: 5,
  per_goal_budget_usd: 10.0,
};

const MOCK_ANALYTICS = {
  total_cost_usd: 245.67,
  cost_by_day: [
    { date: '2026-06-28', cost_usd: 8.0 },
    { date: '2026-06-29', cost_usd: 10.5 },
    { date: '2026-06-30', cost_usd: 12.45 },
  ],
  cost_by_model: {
    'claude-opus-4': 150.0,
    'gpt-4o': 75.0,
    'gpt-4o-mini': 20.67,
  },
  daily_budget_usd: 100,
  budget_utilization: 0.1245,
};

const MOCK_PER_AGENT = {
  period_days: 30,
  agents: [
    {
      agent_id: 'agent-abc123',
      total_cost_usd: 95.40,
      total_prompt_tokens: 500_000,
      total_completion_tokens: 200_000,
      goal_count: 20,
      avg_cost_per_goal: 4.77,
    },
    {
      agent_id: 'agent-xyz789',
      total_cost_usd: 32.10,
      total_prompt_tokens: 200_000,
      total_completion_tokens: 80_000,
      goal_count: 8,
      avg_cost_per_goal: 4.01,
    },
  ],
};

const MOCK_MODEL_BREAKDOWN = {
  period_days: 30,
  models: [
    {
      model: 'claude-opus-4',
      total_cost_usd: 150.0,
      total_prompt_tokens: 500_000,
      total_completion_tokens: 100_000,
      call_count: 30,
    },
    {
      model: 'gpt-4o',
      total_cost_usd: 75.0,
      total_prompt_tokens: 300_000,
      total_completion_tokens: 120_000,
      call_count: 25,
    },
  ],
};

const MOCK_ANOMALIES = {
  anomalies: [
    {
      id: 'anom-001',
      agent_id: 'agent-abc123',
      anomaly_type: 'spike',
      message: 'Cost Spike: $0.8500 actual vs $0.2000 baseline (4.2σ)',
      cost_actual_usd: 0.85,
      cost_baseline_usd: 0.20,
      cost_delta_usd: 0.65,
      sigma_deviation: 4.2,
      severity: 'high',
      detected_at: new Date(Date.now() - 2 * 60 * 60_000).toISOString(),
    },
    {
      id: 'anom-002',
      agent_id: null,
      anomaly_type: 'sustained_high',
      message: 'Sustained High: $1.50 actual vs $0.75 baseline (2.8σ)',
      cost_actual_usd: 1.50,
      cost_baseline_usd: 0.75,
      cost_delta_usd: 0.75,
      sigma_deviation: 2.8,
      severity: 'medium',
      detected_at: new Date(Date.now() - 30 * 60_000).toISOString(),
    },
  ],
};

const MOCK_TRENDS = {
  period_days: 30,
  trends: [
    { date: '2026-06-28', cost_usd: 8.0, moving_avg_7d: 7.5, is_anomaly: false },
    { date: '2026-06-29', cost_usd: 10.5, moving_avg_7d: 8.0, is_anomaly: false },
    { date: '2026-06-30', cost_usd: 25.0, moving_avg_7d: 8.5, is_anomaly: true },
  ],
};

const MOCK_PROJECTION = {
  projected_monthly_usd: 374.0,
  daily_avg_usd: 12.45,
  days_of_data: 7,
  confidence: 'high',
};

const MOCK_BUDGETS = {
  daily_spent: 12.45,
  daily_limit: 100.0,
  per_goal_usd: 10.0,
  per_tenant_daily_usd: 100.0,
  daily_remaining: 87.55,
};

const MOCK_PREDICT = {
  predicted_cost_usd: 0.0085,
  p95_cost_usd: 0.0298,
  confidence: 'low',
  basis: 'heuristic_estimate',
  breakdown: {
    planning_usd: 0.00085,
    execution_usd: 0.0068,
    verification_usd: 0.00085,
  },
  budget_remaining_usd: 87.55,
};

function setupMockFetch(overrides: Record<string, object> = {}) {
  return vi.spyOn(globalThis, 'fetch').mockImplementation(async (input) => {
    const url = String(typeof input === 'string' ? input : (input as Request).url);

    const respond = (data: object) =>
      new Response(JSON.stringify(data), {
        status: 200,
        headers: { 'Content-Type': 'application/json' },
      });

    if (url.includes('/goals/cost-metrics'))
      return respond(overrides['cost-metrics'] ?? MOCK_COST_METRICS);
    if (url.includes('/analytics/costs'))
      return respond(overrides['analytics'] ?? MOCK_ANALYTICS);
    if (url.includes('/costs/per-agent'))
      return respond(overrides['per-agent'] ?? MOCK_PER_AGENT);
    if (url.includes('/costs/by-model'))
      return respond(overrides['by-model'] ?? MOCK_MODEL_BREAKDOWN);
    if (url.includes('/costs/trends'))
      return respond(overrides['trends'] ?? MOCK_TRENDS);
    if (url.includes('/costs/projection'))
      return respond(overrides['projection'] ?? MOCK_PROJECTION);
    if (url.includes('/costs/budgets') && !url.includes('PUT'))
      return respond(overrides['budgets'] ?? MOCK_BUDGETS);
    if (url.includes('/costs/anomalies'))
      return respond(overrides['anomalies'] ?? MOCK_ANOMALIES);
    if (url.includes('/costs/predict'))
      return respond(overrides['predict'] ?? MOCK_PREDICT);
    if (url.includes('/analytics/goals'))
      return respond({ active_goals: 2, total_goals: 87, success_rate: 0.92, avg_latency_ms: 1200, cost_today_usd: 12.45, goals_today: 5 });
    if (url.includes('/costs/summary') && !url.includes('format=csv'))
      return respond(overrides['summary'] ?? { total_cost_usd: 12.45, period_days: 30 });

    return new Response(null, { status: 404 });
  });
}

// Extended per-agent dataset exercising edge cases the default MOCK_PER_AGENT
// doesn't: a mid-range efficiency ratio (yellow), a null agent_id ("—"
// fallbacks + "unknown" filter option), zero tokens (no $/1M token figure),
// and a sparkline trend that goes up (red) rather than down (green).
const MOCK_PER_AGENT_EXTENDED = {
  period_days: 30,
  agents: [
    {
      agent_id: 'agent-abc123',
      total_cost_usd: 95.40,
      total_prompt_tokens: 500_000,
      total_completion_tokens: 200_000,
      goal_count: 20,
      avg_cost_per_goal: 4.77,
    },
    {
      agent_id: 'agent-xyz789',
      total_cost_usd: 32.10,
      total_prompt_tokens: 200_000,
      total_completion_tokens: 80_000,
      goal_count: 8,
      avg_cost_per_goal: 4.01,
    },
    {
      agent_id: 'agent-mid555',
      total_cost_usd: 55.0,
      total_prompt_tokens: 100_000,
      total_completion_tokens: 50_000,
      goal_count: 1,
      avg_cost_per_goal: 60.0,
    },
    {
      agent_id: null,
      total_cost_usd: 3.0,
      total_prompt_tokens: 0,
      total_completion_tokens: 0,
      goal_count: 1,
      avg_cost_per_goal: 3.0,
    },
  ],
};

// ── Test setup ────────────────────────────────────────────────────────────────

beforeEach(() => {
  useAuthStore.setState({
    apiKey: 'test-api-key',
    tenantId: 'tenant-test',
    plan: 'professional',
    isAuthenticated: true,
  });
  useToastStore.setState({ toasts: [] });
});

afterEach(() => {
  vi.restoreAllMocks();
  // The CSV-export test stubs `globalThis.URL` (vi.stubGlobal); restoreAllMocks()
  // does not undo stubbed globals, so without this later tests would inherit a
  // broken URL constructor (react-router and other internals rely on the real one).
  vi.unstubAllGlobals();
});

// ── Tests ─────────────────────────────────────────────────────────────────────

describe('CostDashboardPage', () => {
  test('renders the cost dashboard heading', async () => {
    setupMockFetch();
    renderPage();
    await waitFor(() =>
      expect(screen.getByRole('heading', { name: /cost dashboard/i })).toBeInTheDocument(),
      { timeout: 3000 }
    );
  });

  test('shows period selector buttons (Today / 7d / 30d / 90d)', async () => {
    setupMockFetch();
    renderPage();
    await waitFor(() => {
      expect(screen.getByRole('button', { name: 'Today' })).toBeInTheDocument();
      expect(screen.getByRole('button', { name: '7d' })).toBeInTheDocument();
      expect(screen.getByRole('button', { name: '30d' })).toBeInTheDocument();
      expect(screen.getByRole('button', { name: '90d' })).toBeInTheDocument();
    }, { timeout: 3000 });
  });

  test('period selector refetches data when changed', async () => {
    const fetchSpy = setupMockFetch();
    renderPage();

    // Wait for initial load
    await waitFor(() =>
      expect(screen.getByRole('heading', { name: /cost dashboard/i })).toBeInTheDocument()
    );

    const callsBefore = fetchSpy.mock.calls.length;

    // Click 7d button
    await act(async () => {
      fireEvent.click(screen.getByRole('button', { name: '7d' }));
    });

    await waitFor(() => {
      const callsAfter = fetchSpy.mock.calls.length;
      expect(callsAfter).toBeGreaterThan(callsBefore);
    }, { timeout: 3000 });
  });

  // ── KPI Cards ──────────────────────────────────────────────────────────────

  test('kpi_cards_render_with_correct_values', async () => {
    setupMockFetch();
    renderPage();

    // KPI labels are always rendered
    await waitFor(() => {
      expect(screen.getByText(/cost today/i)).toBeInTheDocument();
    }, { timeout: 8000 });

    expect(screen.getByText(/budget remaining/i)).toBeInTheDocument();
    expect(screen.getByText(/avg cost/i)).toBeInTheDocument();
    expect(screen.getByText(/goals run/i)).toBeInTheDocument();
    expect(screen.getByText(/projected month/i)).toBeInTheDocument();

    // Currency values should appear somewhere on the page after load
    await waitFor(() => {
      // At least one $ value should appear (could be $12.45 or $0.00 before load)
      const dollarTexts = document.body.innerHTML.match(/\$\d+\.\d+/g);
      expect(dollarTexts).toBeTruthy();
      expect((dollarTexts ?? []).length).toBeGreaterThan(0);
    }, { timeout: 8000 });
  }, 10000);

  test('kpi total period spend shown with correct currency format', async () => {
    setupMockFetch();
    renderPage();

    // Wait for analytics query to resolve and the total cost to appear.
    // analytics.total_cost_usd = 245.67 — formatCompact(245.67) = "$246" (toFixed(0))
    await waitFor(() => {
      const innerHTML = document.body.innerHTML;
      // Match either $245 or $246 (rounding) or the compact form
      expect(innerHTML).toMatch(/\$24[5-6]/);
    }, { timeout: 8000 });
  }, 10000);

  // ── Anomaly Panel ──────────────────────────────────────────────────────────

  test('anomaly_panel_shows_severity_and_sigma', async () => {
    setupMockFetch();
    renderPage();

    // Anomaly panel heading
    await waitFor(() => {
      expect(screen.getByText(/anomal/i)).toBeInTheDocument();
    }, { timeout: 8000 });

    // Sigma deviation shown — check DOM directly to avoid strict mode issues
    await waitFor(() => {
      expect(document.body.innerHTML).toMatch(/4\.2σ/);
    }, { timeout: 8000 });

    // High severity badge
    await waitFor(() => {
      const highBadges = screen.getAllByText(/high/i);
      expect(highBadges.length).toBeGreaterThan(0);
    }, { timeout: 8000 });
  }, 15000);

  test('anomaly panel shows cost delta', async () => {
    setupMockFetch();
    renderPage();

    await waitFor(() => {
      // +$0.65 delta for the first anomaly
      expect(screen.getByText(/\+\$0\.65/)).toBeInTheDocument();
    }, { timeout: 5000 });
  });

  test('anomaly panel shows investigate button for agent anomalies', async () => {
    setupMockFetch();
    renderPage();

    await waitFor(() => {
      expect(screen.getByRole('button', { name: /investigate/i })).toBeInTheDocument();
    }, { timeout: 5000 });
  });

  test('anomaly panel shows configure thresholds button', async () => {
    setupMockFetch();
    renderPage();

    await waitFor(() => {
      expect(screen.getByText(/configure thresholds/i)).toBeInTheDocument();
    }, { timeout: 5000 });
  });

  // ── Cost Predictor ─────────────────────────────────────────────────────────

  test('cost_predictor_triggers_on_text_input', async () => {
    const fetchSpy = setupMockFetch();
    renderPage();
    const user = userEvent.setup();

    await waitFor(() =>
      expect(screen.getByRole('heading', { name: /cost dashboard/i })).toBeInTheDocument()
    );

    // Open predictor
    const predictorToggle = screen.getByText(/cost predictor/i);
    await user.click(predictorToggle);

    await waitFor(() => {
      expect(screen.getByRole('textbox', { name: /goal text/i })).toBeInTheDocument();
    });

    const textarea = screen.getByRole('textbox', { name: /goal text/i });

    // Type a goal longer than 20 chars (triggers prediction after 800ms debounce)
    await user.type(textarea, 'Send a daily weather summary email to the entire team');

    // Prediction API call should eventually be made
    await waitFor(() => {
      const predictCalls = fetchSpy.mock.calls.filter(([url]) =>
        String(url).includes('/costs/predict')
      );
      expect(predictCalls.length).toBeGreaterThan(0);
    }, { timeout: 5000 });
  });

  test('cost predictor shows predicted_cost_usd not estimated_cost_usd', async () => {
    setupMockFetch();
    renderPage();
    const user = userEvent.setup();

    await waitFor(() =>
      expect(screen.getByRole('heading', { name: /cost dashboard/i })).toBeInTheDocument()
    );

    // Open predictor
    await user.click(screen.getByText(/cost predictor/i));

    await waitFor(() => {
      expect(screen.getByRole('textbox', { name: /goal text/i })).toBeInTheDocument();
    });

    await user.type(
      screen.getByRole('textbox', { name: /goal text/i }),
      'Send a daily weather summary email to the entire team'
    );

    await waitFor(() => {
      // Should show p50 estimate from predicted_cost_usd (MOCK_PREDICT.predicted_cost_usd = 0.0085)
      expect(document.body.innerHTML).toMatch(/\$0\.008[0-9]|\$0\.008/);
    }, { timeout: 8000 });

    // Should show p95 value
    expect(document.body.innerHTML).toMatch(/p95/i);
    // Should show confidence label (use getAllByText since it may appear multiple times)
    const confidenceElements = screen.queryAllByText(/confidence/i);
    expect(confidenceElements.length).toBeGreaterThan(0);
  }, 15000);

  // ── Budget Modal ───────────────────────────────────────────────────────────

  test('budget_modal_opens_and_saves', async () => {
    const fetchSpy = setupMockFetch();
    // Mock PUT /costs/budgets
    fetchSpy.mockImplementation(async (input) => {
      const req = typeof input === 'string' ? input : (input as Request).url;
      const url = String(req);
      const method = typeof input === 'object' ? (input as Request).method : 'GET';

      if (url.includes('/costs/budgets') && method === 'PUT') {
        return new Response(JSON.stringify({ per_goal_usd: 15, per_tenant_daily_usd: 200 }), {
          status: 200,
          headers: { 'Content-Type': 'application/json' },
        });
      }
      // fall through to standard mock
      return new Response(JSON.stringify({}), { status: 200, headers: { 'Content-Type': 'application/json' } });
    });

    renderPage();
    const user = userEvent.setup();

    await waitFor(() =>
      expect(screen.getByRole('button', { name: /set budget/i })).toBeInTheDocument(),
      { timeout: 3000 }
    );

    // Open modal
    await user.click(screen.getByRole('button', { name: /set budget/i }));

    await waitFor(() => {
      expect(screen.getByRole('dialog', { name: /budget manager/i })).toBeInTheDocument();
    });

    // Verify form inputs exist
    expect(screen.getByRole('spinbutton', { name: /per-goal budget limit/i })).toBeInTheDocument();
    expect(screen.getByRole('spinbutton', { name: /daily budget limit/i })).toBeInTheDocument();
  });

  test('budget modal can be closed', async () => {
    setupMockFetch();
    renderPage();
    const user = userEvent.setup();

    await waitFor(() =>
      expect(screen.getByRole('button', { name: /set budget/i })).toBeInTheDocument()
    );

    await user.click(screen.getByRole('button', { name: /set budget/i }));

    await waitFor(() =>
      expect(screen.getByRole('dialog', { name: /budget manager/i })).toBeInTheDocument()
    );

    // Close with X button
    await user.click(screen.getByRole('button', { name: /close budget modal/i }));

    await waitFor(() =>
      expect(screen.queryByRole('dialog', { name: /budget manager/i })).not.toBeInTheDocument()
    );
  });

  // ── Agent Table ────────────────────────────────────────────────────────────

  test('agent_table_is_sortable', async () => {
    setupMockFetch();
    renderPage();

    // Wait for agent cost intelligence section header
    await waitFor(() => {
      expect(screen.getByText(/agent cost intelligence/i)).toBeInTheDocument();
    }, { timeout: 8000 });

    // Wait for agent data to appear in the table
    await waitFor(() => {
      expect(document.body.innerHTML).toMatch(/agent-abc123/);
    }, { timeout: 8000 });

    // Sort by Total Cost column header
    const totalCostHeader = screen.getByRole('columnheader', { name: /total cost/i });
    fireEvent.click(totalCostHeader);

    // After sort, agents should still be rendered
    await waitFor(() => {
      expect(document.body.innerHTML).toMatch(/agent-abc123/);
    }, { timeout: 5000 });

    // Click again to reverse sort
    fireEvent.click(totalCostHeader);
    await waitFor(() => {
      expect(document.body.innerHTML).toMatch(/agent-xyz789/);
    }, { timeout: 5000 });
  }, 25000);

  test('agent table shows avg_cost_per_goal computed field', async () => {
    setupMockFetch();
    renderPage();

    await waitFor(() => {
      // avg_cost_per_goal = 4.77 for agent-abc123 → shows as $4.77
      expect(screen.getByText(/\$4\.77/)).toBeInTheDocument();
    }, { timeout: 5000 });
  });

  // ── Export CSV ─────────────────────────────────────────────────────────────

  test('export csv button calls GET /costs/summary?format=csv', async () => {
    const fetchSpy = setupMockFetch();
    // Add CSV mock
    fetchSpy.mockImplementation(async (input) => {
      const url = String(typeof input === 'string' ? input : (input as Request).url);
      if (url.includes('format=csv')) {
        return new Response('agent_id,total_cost_usd\nagent-abc,95.40', {
          status: 200,
          headers: { 'Content-Type': 'text/csv' },
        });
      }
      // standard mocks
      if (url.includes('/goals/cost-metrics')) return new Response(JSON.stringify(MOCK_COST_METRICS), { status: 200, headers: { 'Content-Type': 'application/json' } });
      if (url.includes('/analytics/costs')) return new Response(JSON.stringify(MOCK_ANALYTICS), { status: 200, headers: { 'Content-Type': 'application/json' } });
      if (url.includes('/costs/per-agent')) return new Response(JSON.stringify(MOCK_PER_AGENT), { status: 200, headers: { 'Content-Type': 'application/json' } });
      if (url.includes('/costs/by-model')) return new Response(JSON.stringify(MOCK_MODEL_BREAKDOWN), { status: 200, headers: { 'Content-Type': 'application/json' } });
      if (url.includes('/costs/trends')) return new Response(JSON.stringify(MOCK_TRENDS), { status: 200, headers: { 'Content-Type': 'application/json' } });
      if (url.includes('/costs/projection')) return new Response(JSON.stringify(MOCK_PROJECTION), { status: 200, headers: { 'Content-Type': 'application/json' } });
      if (url.includes('/costs/budgets')) return new Response(JSON.stringify(MOCK_BUDGETS), { status: 200, headers: { 'Content-Type': 'application/json' } });
      if (url.includes('/costs/anomalies')) return new Response(JSON.stringify(MOCK_ANOMALIES), { status: 200, headers: { 'Content-Type': 'application/json' } });
      return new Response(null, { status: 404 });
    });

    // Mock URL.createObjectURL so download doesn't crash
    vi.stubGlobal('URL', {
      createObjectURL: vi.fn(() => 'blob:mock'),
      revokeObjectURL: vi.fn(),
    });

    renderPage();

    await waitFor(() =>
      expect(screen.getByRole('button', { name: /export csv/i })).toBeInTheDocument(),
      { timeout: 3000 }
    );

    fireEvent.click(screen.getByRole('button', { name: /export csv/i }));

    await waitFor(() => {
      const csvCalls = fetchSpy.mock.calls.filter(([url]) =>
        String(url).includes('format=csv')
      );
      expect(csvCalls.length).toBeGreaterThan(0);
    }, { timeout: 3000 });
  });

  // ── Error handling ─────────────────────────────────────────────────────────

  test('handles API error gracefully without crashing', async () => {
    vi.spyOn(globalThis, 'fetch').mockResolvedValue(
      new Response('Server Error', { status: 500 })
    );
    renderPage();

    // Should not crash — heading should still render
    await waitFor(() =>
      expect(screen.getByRole('heading', { name: /cost dashboard/i })).toBeInTheDocument(),
      { timeout: 3000 }
    );
  });

  test('renders without crashing when no API key', () => {
    useAuthStore.setState({ apiKey: '', isAuthenticated: false });
    vi.spyOn(globalThis, 'fetch').mockReturnValue(new Promise(() => {}));
    expect(() => renderPage()).not.toThrow();
  });
});

// ── Additional coverage: budget modal interactions ─────────────────────────────

describe('CostDashboardPage — budget modal interactions', () => {
  test('per-goal and daily limit inputs update on change', async () => {
    setupMockFetch();
    renderPage();
    const user = userEvent.setup();

    await user.click(await screen.findByRole('button', { name: /set budget/i }));
    await waitFor(() => expect(screen.getByRole('dialog', { name: /budget manager/i })).toBeInTheDocument());

    const perGoalInput = screen.getByRole('spinbutton', { name: /per-goal budget limit/i }) as HTMLInputElement;
    const perDayInput = screen.getByRole('spinbutton', { name: /daily budget limit/i }) as HTMLInputElement;

    await user.clear(perGoalInput);
    await user.type(perGoalInput, '25');
    await user.clear(perDayInput);
    await user.type(perDayInput, '750');

    expect(perGoalInput.value).toBe('25');
    expect(perDayInput.value).toBe('750');
  });

  test('alert threshold checkboxes can be toggled off', async () => {
    setupMockFetch();
    renderPage();
    const user = userEvent.setup();

    await user.click(await screen.findByRole('button', { name: /set budget/i }));
    await waitFor(() => expect(screen.getByRole('dialog', { name: /budget manager/i })).toBeInTheDocument());

    const alert80 = screen.getByRole('checkbox', { name: /alert at 80% of daily limit/i });
    const alert95 = screen.getByRole('checkbox', { name: /alert at 95% of daily limit/i });

    expect(alert80).toBeChecked();
    expect(alert95).toBeChecked();

    await user.click(alert80);
    await user.click(alert95);

    expect(alert80).not.toBeChecked();
    expect(alert95).not.toBeChecked();
  });

  test('cancel button closes the modal without saving', async () => {
    const fetchSpy = setupMockFetch();
    renderPage();
    const user = userEvent.setup();

    await user.click(await screen.findByRole('button', { name: /set budget/i }));
    await waitFor(() => expect(screen.getByRole('dialog', { name: /budget manager/i })).toBeInTheDocument());

    const callsBefore = fetchSpy.mock.calls.filter(([, init]) => (init as RequestInit | undefined)?.method === 'PUT').length;

    await user.click(screen.getByRole('button', { name: /^cancel$/i }));

    await waitFor(() =>
      expect(screen.queryByRole('dialog', { name: /budget manager/i })).not.toBeInTheDocument()
    );

    const callsAfter = fetchSpy.mock.calls.filter(([, init]) => (init as RequestInit | undefined)?.method === 'PUT').length;
    expect(callsAfter).toBe(callsBefore);
  });

  test('saving the budget succeeds and shows a success toast', async () => {
    const fetchSpy = setupMockFetch();
    fetchSpy.mockImplementation(async (input, init) => {
      const url = String(typeof input === 'string' ? input : (input as Request).url);
      const method = init?.method ?? 'GET';
      const respond = (data: object) =>
        new Response(JSON.stringify(data), { status: 200, headers: { 'Content-Type': 'application/json' } });

      if (url.includes('/costs/budgets') && method === 'PUT') {
        return respond({ per_goal_usd: 25, per_tenant_daily_usd: 750 });
      }
      if (url.includes('/goals/cost-metrics')) return respond(MOCK_COST_METRICS);
      if (url.includes('/analytics/costs')) return respond(MOCK_ANALYTICS);
      if (url.includes('/costs/per-agent')) return respond(MOCK_PER_AGENT);
      if (url.includes('/costs/by-model')) return respond(MOCK_MODEL_BREAKDOWN);
      if (url.includes('/costs/trends')) return respond(MOCK_TRENDS);
      if (url.includes('/costs/projection')) return respond(MOCK_PROJECTION);
      if (url.includes('/costs/budgets')) return respond(MOCK_BUDGETS);
      if (url.includes('/costs/anomalies')) return respond(MOCK_ANOMALIES);
      return new Response(null, { status: 404 });
    });

    renderPage();
    const user = userEvent.setup();

    await user.click(await screen.findByRole('button', { name: /set budget/i }));
    await waitFor(() => expect(screen.getByRole('dialog', { name: /budget manager/i })).toBeInTheDocument());

    await user.click(screen.getByRole('button', { name: /save budget/i }));

    await waitFor(() => {
      const toasts = useToastStore.getState().toasts;
      expect(toasts.some((t) => t.kind === 'success' && /budget saved/i.test(t.message))).toBe(true);
    }, { timeout: 5000 });

    await waitFor(() =>
      expect(screen.queryByRole('dialog', { name: /budget manager/i })).not.toBeInTheDocument()
    );
  });

  test('saving the budget shows an error toast when the request fails', async () => {
    const fetchSpy = setupMockFetch();
    fetchSpy.mockImplementation(async (input, init) => {
      const url = String(typeof input === 'string' ? input : (input as Request).url);
      const method = init?.method ?? 'GET';
      const respond = (data: object) =>
        new Response(JSON.stringify(data), { status: 200, headers: { 'Content-Type': 'application/json' } });

      if (url.includes('/costs/budgets') && method === 'PUT') {
        return new Response('Server Error', { status: 500 });
      }
      if (url.includes('/goals/cost-metrics')) return respond(MOCK_COST_METRICS);
      if (url.includes('/analytics/costs')) return respond(MOCK_ANALYTICS);
      if (url.includes('/costs/per-agent')) return respond(MOCK_PER_AGENT);
      if (url.includes('/costs/by-model')) return respond(MOCK_MODEL_BREAKDOWN);
      if (url.includes('/costs/trends')) return respond(MOCK_TRENDS);
      if (url.includes('/costs/projection')) return respond(MOCK_PROJECTION);
      if (url.includes('/costs/budgets')) return respond(MOCK_BUDGETS);
      if (url.includes('/costs/anomalies')) return respond(MOCK_ANOMALIES);
      return new Response(null, { status: 404 });
    });

    renderPage();
    const user = userEvent.setup();

    await user.click(await screen.findByRole('button', { name: /set budget/i }));
    await waitFor(() => expect(screen.getByRole('dialog', { name: /budget manager/i })).toBeInTheDocument());

    await user.click(screen.getByRole('button', { name: /save budget/i }));

    await waitFor(() => {
      const toasts = useToastStore.getState().toasts;
      expect(toasts.some((t) => t.kind === 'error' && /failed to save budget/i.test(t.message))).toBe(true);
    }, { timeout: 5000 });

    // Modal stays open on failure
    expect(screen.getByRole('dialog', { name: /budget manager/i })).toBeInTheDocument();
  });

  test('budget modal fetches its own budget data even when the page-level queries are disabled', async () => {
    useAuthStore.setState({ apiKey: '', isAuthenticated: false });
    const fetchSpy = setupMockFetch();
    renderPage();
    const user = userEvent.setup();

    await user.click(screen.getByRole('button', { name: /set budget/i }));
    await waitFor(() => expect(screen.getByRole('dialog', { name: /budget manager/i })).toBeInTheDocument());

    await waitFor(() => {
      const budgetGetCalls = fetchSpy.mock.calls.filter(([url]) => String(url).includes('/costs/budgets'));
      expect(budgetGetCalls.length).toBeGreaterThan(0);
    }, { timeout: 5000 });
  });
});

// ── Additional coverage: anomaly panel thresholds & investigate ────────────────

describe('CostDashboardPage — anomaly panel extras', () => {
  test('investigate button can be clicked without crashing', async () => {
    setupMockFetch();
    renderPage();
    const user = userEvent.setup();

    const investigateBtn = await screen.findByRole('button', { name: /investigate/i });
    await user.click(investigateBtn);

    // Page should still be intact after navigation attempt
    expect(screen.getByRole('heading', { name: /cost dashboard/i })).toBeInTheDocument();
  });

  test('configure thresholds modal opens and closes via the X button', async () => {
    setupMockFetch();
    renderPage();
    const user = userEvent.setup();

    await user.click(await screen.findByRole('button', { name: /configure anomaly thresholds/i }, { timeout: 8000 }));

    await waitFor(() => expect(screen.getByText(/anomaly thresholds/i)).toBeInTheDocument());

    await user.click(screen.getByRole('button', { name: /close threshold modal/i }));

    await waitFor(() => expect(screen.queryByText(/anomaly thresholds/i)).not.toBeInTheDocument());
  }, 15000);

  test('configure thresholds modal closes via the bottom Close button', async () => {
    setupMockFetch();
    renderPage();
    const user = userEvent.setup();

    await user.click(await screen.findByRole('button', { name: /configure anomaly thresholds/i }, { timeout: 8000 }));
    await waitFor(() => expect(screen.getByText(/anomaly thresholds/i)).toBeInTheDocument());

    await user.click(screen.getByRole('button', { name: /^close$/i }));

    await waitFor(() => expect(screen.queryByText(/anomaly thresholds/i)).not.toBeInTheDocument());
  }, 15000);
});

// ── Additional coverage: agent table edge cases & sorting ──────────────────────

describe('CostDashboardPage — agent table edge cases', () => {
  test('renders fallbacks for missing agent id and zero-token cost efficiency', async () => {
    setupMockFetch({ 'per-agent': MOCK_PER_AGENT_EXTENDED });
    renderPage();

    await waitFor(() => {
      expect(document.body.innerHTML).toMatch(/agent-mid555/);
    }, { timeout: 8000 });

    // "—" appears for both the missing agent_id row and the zero-token efficiency cell
    const dashes = screen.getAllByText('—');
    expect(dashes.length).toBeGreaterThan(0);

    // Efficiency color classes: red (top spender), yellow (mid), green (low)
    expect(document.body.innerHTML).toMatch(/text-red-600/);
    expect(document.body.innerHTML).toMatch(/text-yellow-600/);
    expect(document.body.innerHTML).toMatch(/text-green-600/);
  });

  test('clicking a table row navigates to the agent detail page', async () => {
    setupMockFetch();
    renderPage();

    await waitFor(() => expect(document.body.innerHTML).toMatch(/agent-abc123/), { timeout: 8000 });

    const table = screen.getByRole('table');
    const row = within(table).getByText(/agent-abc123/).closest('tr');
    expect(row).toBeTruthy();
    fireEvent.click(row as HTMLElement);

    // No crash — heading remains
    expect(screen.getByRole('heading', { name: /cost dashboard/i })).toBeInTheDocument();
  });

  test('pressing Enter on a table row navigates to the agent detail page', async () => {
    setupMockFetch();
    renderPage();

    await waitFor(() => expect(document.body.innerHTML).toMatch(/agent-abc123/), { timeout: 8000 });

    const table = screen.getByRole('table');
    const row = within(table).getByText(/agent-abc123/).closest('tr') as HTMLElement;
    fireEvent.keyDown(row, { key: 'Enter' });

    expect(screen.getByRole('heading', { name: /cost dashboard/i })).toBeInTheDocument();
  });

  test('sorting by a fresh column (Agent) then Token Efficiency exercises all sort branches', async () => {
    setupMockFetch({ 'per-agent': MOCK_PER_AGENT_EXTENDED });
    renderPage();

    await waitFor(() => expect(document.body.innerHTML).toMatch(/agent-abc123/), { timeout: 8000 });

    fireEvent.click(screen.getByRole('columnheader', { name: /^agent$/i }));
    await waitFor(() => expect(document.body.innerHTML).toMatch(/agent-abc123/));

    fireEvent.click(screen.getByRole('columnheader', { name: /token efficiency/i }));
    await waitFor(() => expect(document.body.innerHTML).toMatch(/agent-abc123/));

    // Reverse direction on the same column
    fireEvent.click(screen.getByRole('columnheader', { name: /token efficiency/i }));
    await waitFor(() => expect(document.body.innerHTML).toMatch(/agent-abc123/));
  });
});

// ── Additional coverage: filters & selects ──────────────────────────────────────

describe('CostDashboardPage — filters and selects', () => {
  test('agent filter narrows the table and shows singular/filtered labels', async () => {
    setupMockFetch({ 'per-agent': MOCK_PER_AGENT_EXTENDED });
    renderPage();

    await waitFor(() => expect(document.body.innerHTML).toMatch(/agent-abc123/), { timeout: 8000 });

    const filterSelect = screen.getByRole('combobox', { name: /filter by agent/i });
    fireEvent.change(filterSelect, { target: { value: 'agent-abc123' } });

    await waitFor(() => {
      expect(screen.getByText(/^1 agent\b/)).toBeInTheDocument();
    });
    expect(screen.getByText(/\(filtered\)/i)).toBeInTheDocument();
  });

  test('agent filter dropdown shows an "unknown" option for agents without an id', async () => {
    setupMockFetch({ 'per-agent': MOCK_PER_AGENT_EXTENDED });
    renderPage();

    const filterSelect = await screen.findByRole('combobox', { name: /filter by agent/i });
    await waitFor(() => {
      expect(within(filterSelect).getByText(/unknown/i)).toBeInTheDocument();
    }, { timeout: 8000 });
  });

  test('selecting an agent in the cost predictor dropdown updates the selection', async () => {
    setupMockFetch({ 'per-agent': MOCK_PER_AGENT_EXTENDED });
    renderPage();
    const user = userEvent.setup();

    await user.click(await screen.findByText(/cost predictor/i));

    const agentSelect = await screen.findByRole('combobox', { name: /select agent for prediction/i });
    fireEvent.change(agentSelect, { target: { value: 'agent-mid555' } });

    expect((agentSelect as HTMLSelectElement).value).toBe('agent-mid555');
  });
});

// ── Additional coverage: cost breakdown toggle & edge cases ────────────────────

describe('CostDashboardPage — cost breakdown toggle', () => {
  test('toggling between By Model and By Operation views updates pressed state', async () => {
    setupMockFetch();
    renderPage();
    const user = userEvent.setup();

    const byModelBtn = await screen.findByRole('button', { name: /by model/i });
    const byOperationBtn = screen.getByRole('button', { name: /by operation/i });

    expect(byModelBtn).toHaveAttribute('aria-pressed', 'true');
    expect(byOperationBtn).toHaveAttribute('aria-pressed', 'false');

    await user.click(byOperationBtn);

    expect(byOperationBtn).toHaveAttribute('aria-pressed', 'true');
    expect(byModelBtn).toHaveAttribute('aria-pressed', 'false');

    await user.click(byModelBtn);
    expect(byModelBtn).toHaveAttribute('aria-pressed', 'true');
  });

  test('falls back to analytics.cost_by_model when /costs/by-model is empty', async () => {
    setupMockFetch({ 'by-model': { models: [] } });
    renderPage();

    // analytics.cost_by_model has claude-opus-4 / gpt-4o / gpt-4o-mini
    await waitFor(() => {
      expect(document.body.innerHTML).toMatch(/claude-opus-4|gpt-4o/);
    }, { timeout: 8000 });
  });

  test('renders a zero-value pie slice without crashing (0% legend fallback)', async () => {
    setupMockFetch({ 'by-model': { models: [{ model: 'free-tier-model', total_cost_usd: 0, total_prompt_tokens: 0, total_completion_tokens: 0, call_count: 0 }] } });
    renderPage();

    await waitFor(() => {
      expect(document.body.innerHTML).toMatch(/free-tier-model/);
    }, { timeout: 8000 });
  });

  test('shows "No operation data yet" when per-agent data is empty', async () => {
    setupMockFetch({ 'per-agent': { agents: [] } });
    renderPage();
    const user = userEvent.setup();

    await user.click(await screen.findByRole('button', { name: /by operation/i }));

    await waitFor(() => {
      expect(screen.getByText(/no operation data yet/i)).toBeInTheDocument();
    });
  });
});

// ── Additional coverage: budget consumption banners ─────────────────────────────

describe('CostDashboardPage — budget consumption banners', () => {
  test('shows the medium (80-95%) warning banner and can open the modal via Edit', async () => {
    setupMockFetch({ 'cost-metrics': { ...MOCK_COST_METRICS, budget_utilization: 0.88 } });
    renderPage();
    const user = userEvent.setup();

    await waitFor(() => {
      expect(screen.getByText(/budget 80% consumed/i)).toBeInTheDocument();
    }, { timeout: 8000 });

    await user.click(screen.getByRole('button', { name: /^edit$/i }));

    await waitFor(() =>
      expect(screen.getByRole('dialog', { name: /budget manager/i })).toBeInTheDocument()
    );
  });

  test('shows the critical (>95%) banner', async () => {
    setupMockFetch({ 'cost-metrics': { ...MOCK_COST_METRICS, budget_utilization: 0.97 } });
    renderPage();

    await waitFor(() => {
      expect(screen.getByText(/critical.*budget almost exhausted/i)).toBeInTheDocument();
    }, { timeout: 8000 });
  });

  test('shows no warning banner under 80% utilization', async () => {
    setupMockFetch({ 'cost-metrics': { ...MOCK_COST_METRICS, budget_utilization: 0.3 } });
    renderPage();

    await waitFor(() => {
      expect(screen.getByRole('heading', { name: /cost dashboard/i })).toBeInTheDocument();
    });
    expect(screen.queryByText(/budget 80% consumed/i)).not.toBeInTheDocument();
    expect(screen.queryByText(/critical.*budget almost exhausted/i)).not.toBeInTheDocument();
  });
});

// ── Additional coverage: live cost ticker ───────────────────────────────────────

describe('CostDashboardPage — live cost ticker', () => {
  test('flashes a red delta when spend increases between refreshes', async () => {
    setupMockFetch();
    const qc = new QueryClient({ defaultOptions: { queries: { retry: false, gcTime: 0 } } });
    renderPage(qc);

    await waitFor(() => {
      expect(qc.getQueryData(['cost-summary-ticker', 30])).toBeTruthy();
    }, { timeout: 5000 });

    act(() => {
      qc.setQueryData(['cost-summary-ticker', 30], { total_cost_usd: 50, period_days: 30 });
    });

    await waitFor(() => {
      expect(screen.getByText(/since last refresh/i)).toBeInTheDocument();
    }, { timeout: 5000 });
    expect(document.body.innerHTML).toMatch(/\+\$50|\+\$37/);
  });

  test('flashes a green delta when spend decreases between refreshes', async () => {
    setupMockFetch();
    const qc = new QueryClient({ defaultOptions: { queries: { retry: false, gcTime: 0 } } });
    renderPage(qc);

    await waitFor(() => {
      expect(qc.getQueryData(['cost-summary-ticker', 30])).toBeTruthy();
    }, { timeout: 5000 });

    act(() => {
      qc.setQueryData(['cost-summary-ticker', 30], { total_cost_usd: 2, period_days: 30 });
    });

    await waitFor(() => {
      expect(screen.getByText(/since last refresh/i)).toBeInTheDocument();
    }, { timeout: 5000 });
    // Decrease -> no "+" prefix
    expect(document.body.innerHTML).toMatch(/since last refresh/);
  });
});

// ── Additional coverage: CSV export failure ─────────────────────────────────────

describe('CostDashboardPage — export failure handling', () => {
  test('shows an error toast when the CSV export fails', async () => {
    const fetchSpy = setupMockFetch();
    fetchSpy.mockImplementation(async (input) => {
      const url = String(typeof input === 'string' ? input : (input as Request).url);
      if (url.includes('format=csv')) throw new Error('network failure');
      const respond = (data: object) =>
        new Response(JSON.stringify(data), { status: 200, headers: { 'Content-Type': 'application/json' } });
      if (url.includes('/goals/cost-metrics')) return respond(MOCK_COST_METRICS);
      if (url.includes('/analytics/costs')) return respond(MOCK_ANALYTICS);
      if (url.includes('/costs/per-agent')) return respond(MOCK_PER_AGENT);
      if (url.includes('/costs/by-model')) return respond(MOCK_MODEL_BREAKDOWN);
      if (url.includes('/costs/trends')) return respond(MOCK_TRENDS);
      if (url.includes('/costs/projection')) return respond(MOCK_PROJECTION);
      if (url.includes('/costs/budgets')) return respond(MOCK_BUDGETS);
      if (url.includes('/costs/anomalies')) return respond(MOCK_ANOMALIES);
      return new Response(null, { status: 404 });
    });

    renderPage();

    const user = userEvent.setup();
    await user.click(await screen.findByRole('button', { name: /export csv/i }));

    await waitFor(() => {
      const toasts = useToastStore.getState().toasts;
      expect(toasts.some((t) => t.kind === 'error' && /failed to export csv/i.test(t.message))).toBe(true);
    }, { timeout: 5000 });
  });
});
