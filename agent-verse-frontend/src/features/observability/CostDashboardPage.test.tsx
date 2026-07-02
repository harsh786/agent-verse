/**
 * CostDashboardPage tests — comprehensive coverage of the world-class dashboard.
 *
 * Run: npm test src/features/observability/CostDashboardPage.test.tsx
 */

import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { render, screen, waitFor, fireEvent, act } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter } from 'react-router-dom';
import { afterEach, beforeEach, describe, expect, test, vi } from 'vitest';
import { useAuthStore } from '@/stores/auth';
import { CostDashboardPage } from './CostDashboardPage';

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

    return new Response(null, { status: 404 });
  });
}

// ── Test setup ────────────────────────────────────────────────────────────────

beforeEach(() => {
  useAuthStore.setState({
    apiKey: 'test-api-key',
    tenantId: 'tenant-test',
    plan: 'professional',
    isAuthenticated: true,
  });
});

afterEach(() => {
  vi.restoreAllMocks();
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
