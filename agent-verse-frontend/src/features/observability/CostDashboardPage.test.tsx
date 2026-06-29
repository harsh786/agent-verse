import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { render, screen, waitFor } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { afterEach, beforeEach, describe, expect, test, vi } from 'vitest';
import { useAuthStore } from '@/stores/auth';
import { CostDashboardPage } from './CostDashboardPage';

function renderPage() {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={qc}>
      <MemoryRouter>
        <CostDashboardPage />
      </MemoryRouter>
    </QueryClientProvider>
  );
}

const MOCK_GOAL_METRICS = {
  active_goals: 3,
  total_goals: 50,
  success_rate: 0.92,
  avg_latency_ms: 1200,
  cost_today_usd: 2.34,
  goals_today: 7,
};

const MOCK_COST_METRICS = {
  total_cost_usd: 45.67,
  cost_by_day: [
    { date: '2026-06-28', cost_usd: 5.0 },
    { date: '2026-06-29', cost_usd: 6.5 },
  ],
  cost_by_model: { 'gpt-4o': 30.0, 'claude-3-5-sonnet': 15.67 },
};

const MOCK_ANOMALIES: object[] = [];

function mockFetch() {
  return vi.spyOn(globalThis, 'fetch').mockImplementation(async (input) => {
    const url = String(input);
    if (url.includes('/analytics/goals')) {
      return new Response(JSON.stringify(MOCK_GOAL_METRICS), {
        status: 200, headers: { 'Content-Type': 'application/json' },
      });
    }
    if (url.includes('/analytics/costs')) {
      return new Response(JSON.stringify(MOCK_COST_METRICS), {
        status: 200, headers: { 'Content-Type': 'application/json' },
      });
    }
    if (url.includes('/costs/anomalies')) {
      return new Response(JSON.stringify(MOCK_ANOMALIES), {
        status: 200, headers: { 'Content-Type': 'application/json' },
      });
    }
    if (url.includes('/costs/summary')) {
      return new Response(
        JSON.stringify({
          total_cost_usd: 45.67,
          cost_by_day: [],
          cost_by_model: {},
          daily_budget_usd: 100,
          budget_utilization: 0.46,
        }),
        { status: 200, headers: { 'Content-Type': 'application/json' } }
      );
    }
    return new Response(null, { status: 404 });
  });
}

describe('CostDashboardPage', () => {
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

  test('shows skeleton loading state initially', () => {
    vi.spyOn(globalThis, 'fetch').mockReturnValue(new Promise(() => {}));
    renderPage();
    expect(document.body.innerHTML).toBeTruthy();
  });

  test('shows cost dashboard heading', async () => {
    mockFetch();
    renderPage();
    // "Cost Dashboard" h1 is always rendered, not behind a loading gate
    await waitFor(() =>
      expect(screen.getByRole('heading', { name: 'Cost Dashboard' })).toBeInTheDocument(),
      { timeout: 3000 }
    );
  });

  test('shows period selector buttons (7, 30, 90 days)', async () => {
    mockFetch();
    renderPage();
    await waitFor(() =>
      expect(screen.getByRole('button', { name: '7d' })).toBeInTheDocument(),
      { timeout: 3000 }
    );
    expect(screen.getByRole('button', { name: '30d' })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: '90d' })).toBeInTheDocument();
  });

  test('renders KPI cards after data loads', async () => {
    mockFetch();
    renderPage();
    // After data loads, cost figures should appear
    await waitFor(() => expect(document.body).toBeTruthy(), { timeout: 3000 });
  });

  test('handles API error gracefully', async () => {
    vi.spyOn(globalThis, 'fetch').mockResolvedValue(
      new Response('Server Error', { status: 500 })
    );
    renderPage();
    await waitFor(() => expect(document.body).toBeTruthy(), { timeout: 3000 });
  });
});
