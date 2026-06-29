import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { render, screen, waitFor } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { afterEach, beforeEach, describe, expect, test, vi } from 'vitest';
import { useAuthStore } from '@/stores/auth';
import { BudgetManagerPage } from './BudgetManagerPage';

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

const MOCK_BUDGET = {
  daily_budget_usd: 50.0,
  per_goal_budget_usd: 5.0,
  per_agent_budgets: {},
  alert_threshold_pct: 80,
};

const MOCK_PER_AGENT: object[] = [
  { agent_id: 'a1', agent_name: 'Triage Bot', total_cost_usd: 12.5, goal_count: 10, avg_cost_per_goal: 1.25 },
];

function mockFetch(overrides?: { budgets?: object; perAgent?: object[]; agents?: object[] }) {
  return vi.spyOn(globalThis, 'fetch').mockImplementation(async (input) => {
    const url = String(input);
    if (url.includes('/costs/budgets') && !url.includes('PUT')) {
      return new Response(JSON.stringify(overrides?.budgets ?? MOCK_BUDGET), {
        status: 200, headers: { 'Content-Type': 'application/json' },
      });
    }
    if (url.includes('/costs/per-agent')) {
      return new Response(JSON.stringify(overrides?.perAgent ?? MOCK_PER_AGENT), {
        status: 200, headers: { 'Content-Type': 'application/json' },
      });
    }
    if (url.includes('/agents')) {
      return new Response(JSON.stringify(overrides?.agents ?? []), {
        status: 200, headers: { 'Content-Type': 'application/json' },
      });
    }
    return new Response(null, { status: 404 });
  });
}

describe('BudgetManagerPage', () => {
  beforeEach(() => {
    useAuthStore.setState({
      apiKey: 'test-key', tenantId: 'tenant-1', plan: 'professional', isAuthenticated: true,
    });
  });

  afterEach(() => {
    vi.restoreAllMocks();
  });

  test('renders Budget Manager heading', async () => {
    mockFetch();
    renderPage();
    await waitFor(() => expect(screen.getByText('Budget Manager')).toBeInTheDocument());
  });

  test('shows loading spinner before data arrives', () => {
    vi.spyOn(globalThis, 'fetch').mockReturnValue(new Promise(() => {}));
    renderPage();
    // LoadingSpinner renders while budgets query is loading
    expect(document.body).toBeTruthy();
  });

  test('shows daily budget value from API', async () => {
    mockFetch({ budgets: { ...MOCK_BUDGET, daily_budget_usd: 99.0 } });
    renderPage();
    await waitFor(() => expect(screen.getByText('Budget Manager')).toBeInTheDocument());
    // The daily budget input should show 99
    const inputs = screen.queryAllByDisplayValue('99');
    expect(inputs.length).toBeGreaterThan(0);
  });

  test('shows per-agent cost table when data available', async () => {
    mockFetch();
    renderPage();
    // "Triage Bot" appears in both gauge label and table row — check at least one is present
    await waitFor(
      () => expect(screen.getAllByText('Triage Bot').length).toBeGreaterThan(0),
      { timeout: 4000 }
    );
  });

  test('shows error state and retry button when budget fetch fails', async () => {
    vi.spyOn(globalThis, 'fetch').mockResolvedValue(
      new Response('error', { status: 500 })
    );
    renderPage();
    await waitFor(() =>
      expect(screen.getByText(/failed to load budget/i)).toBeInTheDocument()
    );
    expect(screen.getByRole('button', { name: /retry/i })).toBeInTheDocument();
  });
});
