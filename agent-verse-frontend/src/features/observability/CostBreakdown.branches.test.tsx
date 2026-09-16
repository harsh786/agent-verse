import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { render, screen, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, test, vi } from 'vitest';
import { useAuthStore } from '@/stores/auth';
import { CostBreakdown } from './CostBreakdown';

const METRICS = {
  goal_id: 'g-1',
  total_cost_usd: 0.001234,
  roles: [
    { role: 'planner', model: 'claude-x', input_tokens: 1000, output_tokens: 500, cost_usd: 0.0005, calls: 2 },
    { role: 'executor', model: 'gpt-y', input_tokens: 2000, output_tokens: 250, cost_usd: 0.0007, calls: 3 },
  ],
  llm_cache: { hits: 3, misses: 1, hit_rate: 0.75, l1_hits: 2, l2_hits: 1 },
};

function mockFetch(body: unknown, status = 200) {
  return vi.spyOn(globalThis, 'fetch').mockResolvedValue(
    new Response(JSON.stringify(body), { status, headers: { 'Content-Type': 'application/json' } }),
  );
}

function renderCard(goalId = 'g-1') {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={qc}>
      <CostBreakdown goalId={goalId} />
    </QueryClientProvider>,
  );
}

beforeEach(() => {
  useAuthStore.setState({ apiKey: 'k', tenantId: 't', plan: 'free', isAuthenticated: true });
});
afterEach(() => vi.restoreAllMocks());

describe('CostBreakdown — additional branches', () => {
  test('shows the loading state while the metrics request is pending', () => {
    // A never-resolving fetch keeps react-query in the isLoading branch.
    vi.spyOn(globalThis, 'fetch').mockReturnValue(new Promise(() => {}) as Promise<Response>);
    renderCard();
    expect(screen.getByText(/Loading cost data/i)).toBeInTheDocument();
  });

  test('renders the total, one row per role, and the summed tokens + call counts', async () => {
    mockFetch(METRICS);
    renderCard();
    expect(await screen.findByText('Cost Breakdown')).toBeInTheDocument();
    // Total cost formatted to 6 dp.
    expect(screen.getByText('$0.001234')).toBeInTheDocument();
    expect(screen.getByText('planner')).toBeInTheDocument();
    expect(screen.getByText('executor')).toBeInTheDocument();
    // input+output tokens are summed and locale-formatted.
    expect(screen.getByText('1,500')).toBeInTheDocument();
    expect(screen.getByText('2,250')).toBeInTheDocument();
    // per-role cost is rendered at 6 dp.
    expect(screen.getByText('$0.000500')).toBeInTheDocument();
  });

  test('fetches the cost-metrics endpoint with the auth header', async () => {
    const spy = mockFetch(METRICS);
    renderCard('goal-42');
    await screen.findByText('Cost Breakdown');
    await waitFor(() =>
      expect(
        spy.mock.calls.some(([u, i]) =>
          String(u).includes('/goals/goal-42/cost-metrics') &&
          !!((i as RequestInit)?.headers as Record<string, string>),
        ),
      ).toBe(true),
    );
  });

  test('renders the LLM cache summary line when cache stats are present', async () => {
    mockFetch(METRICS);
    renderCard();
    await screen.findByText('Cost Breakdown');
    expect(screen.getByText(/LLM Cache:/i)).toBeInTheDocument();
    // hits / (misses + hits) = 3 / 4 total, 75.0% hit rate.
    expect(screen.getByText(/3 hits \/ 4 total/)).toBeInTheDocument();
    expect(screen.getByText(/75\.0% hit rate/)).toBeInTheDocument();
  });

  test('omits the cache line when llm_cache is absent', async () => {
    mockFetch({ ...METRICS, llm_cache: undefined });
    renderCard();
    await screen.findByText('Cost Breakdown');
    expect(screen.queryByText(/LLM Cache:/i)).not.toBeInTheDocument();
  });

  test('renders nothing when the roles array is empty', async () => {
    mockFetch({ goal_id: 'g-1', total_cost_usd: 0, roles: [] });
    const { container } = renderCard();
    // No roles → component returns null (never shows the heading).
    await waitFor(() => expect(screen.queryByText('Cost Breakdown')).not.toBeInTheDocument());
    expect(container.querySelector('table')).toBeNull();
  });
});
