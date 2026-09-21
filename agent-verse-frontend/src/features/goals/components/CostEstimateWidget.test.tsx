/**
 * Tests for CostEstimateWidget — pre-run cost/time estimate panel.
 * Exercises the enabled/length gate, loading skeleton, error/empty fallthrough,
 * and the confidence/duration/success-probability render branches.
 */
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { render, screen, waitFor } from '@testing-library/react';
import { afterEach, describe, expect, test, vi } from 'vitest';
import { insightsApi, type CostEstimate } from '@/lib/api/client';
import { CostEstimateWidget } from './CostEstimateWidget';

vi.mock('@/lib/api/client', () => ({
  insightsApi: { estimateGoal: vi.fn() },
}));

const estimateGoal = vi.mocked(insightsApi.estimateGoal);

function renderWidget(props: Partial<React.ComponentProps<typeof CostEstimateWidget>> = {}) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={qc}>
      <CostEstimateWidget goal="Deploy the new pricing page to production" {...props} />
    </QueryClientProvider>
  );
}

function makeEstimate(overrides: Partial<CostEstimate> = {}): CostEstimate {
  return {
    estimated_cost_usd: { min: 0.01, mean: 0.05, max: 0.12 },
    estimated_duration_s: { min: 10, mean: 45, max: 90 },
    estimated_iterations: { min: 2, mean: 4, max: 6 },
    success_probability: 0.9,
    similar_goals_count: 3,
    confidence: 'high',
    based_on: 'history',
    ...overrides,
  };
}

afterEach(() => vi.restoreAllMocks());

describe('CostEstimateWidget — gating', () => {
  test('renders nothing when goal text is under 10 characters', () => {
    const { container } = renderWidget({ goal: 'short' });
    expect(container).toBeEmptyDOMElement();
    expect(estimateGoal).not.toHaveBeenCalled();
  });

  test('renders nothing when explicitly disabled, even with a long goal', () => {
    const { container } = renderWidget({ enabled: false });
    expect(container).toBeEmptyDOMElement();
  });
});

describe('CostEstimateWidget — loading and error states', () => {
  test('shows a skeleton while the estimate query is pending', async () => {
    estimateGoal.mockReturnValue(new Promise(() => {})); // never resolves
    const { container } = renderWidget();
    await waitFor(() => {
      expect(container.querySelectorAll('.animate-pulse, [class*="skeleton"], div').length).toBeGreaterThan(0);
    });
    // 3 grid skeletons + 1 header skeleton = 4 Skeleton placeholders
    expect(screen.queryByText('Estimated run')).not.toBeInTheDocument();
  });

  test('renders nothing when the query errors', async () => {
    estimateGoal.mockRejectedValue(new Error('network down'));
    const { container } = renderWidget();
    await waitFor(() => {
      expect(container.querySelector('.rounded-lg.border')).toBeNull();
    });
    expect(screen.queryByText('Estimated run')).not.toBeInTheDocument();
  });

  test('renders nothing when the query resolves with no data', async () => {
    // @ts-expect-error — simulate an empty success payload
    estimateGoal.mockResolvedValue(undefined);
    const { container } = renderWidget();
    await waitFor(() => expect(container.firstChild).toBeNull());
  });
});

describe('CostEstimateWidget — populated estimate', () => {
  test('renders seconds-scale duration, high confidence, and similar-goal count', async () => {
    estimateGoal.mockResolvedValue(makeEstimate({
      estimated_duration_s: { min: 5, mean: 45, max: 60 },
      confidence: 'high',
      similar_goals_count: 7,
    }));
    renderWidget();
    await waitFor(() => expect(screen.getByText('Estimated run')).toBeInTheDocument());
    expect(screen.getByText('~45s')).toBeInTheDocument();
    expect(screen.getByText(/high confidence/)).toBeInTheDocument();
    expect(screen.getByText(/7 similar/)).toBeInTheDocument();
  });

  test('renders minutes-scale duration when mean duration is >= 60s', async () => {
    estimateGoal.mockResolvedValue(makeEstimate({
      estimated_duration_s: { min: 60, mean: 150, max: 240 },
    }));
    renderWidget();
    await waitFor(() => expect(screen.getByText('Estimated run')).toBeInTheDocument());
    // 150s / 60 = 2.5 -> rounds to 3
    expect(screen.getByText('~3m')).toBeInTheDocument();
  });

  test('omits the similar-goals suffix when similar_goals_count is 0', async () => {
    estimateGoal.mockResolvedValue(makeEstimate({ similar_goals_count: 0, confidence: 'low' }));
    renderWidget();
    await waitFor(() => expect(screen.getByText('Estimated run')).toBeInTheDocument());
    expect(screen.getByText(/low confidence/)).toBeInTheDocument();
    expect(screen.queryByText(/similar/)).not.toBeInTheDocument();
  });

  test('renders medium confidence styling', async () => {
    estimateGoal.mockResolvedValue(makeEstimate({ confidence: 'medium', similar_goals_count: 1 }));
    renderWidget();
    await waitFor(() => expect(screen.getByText(/medium confidence/)).toBeInTheDocument());
    expect(screen.getByText(/1 similar/)).toBeInTheDocument();
  });

  test('formats a zero-cost, zero-probability estimate (red success band)', async () => {
    estimateGoal.mockResolvedValue(makeEstimate({
      estimated_cost_usd: { min: 0, mean: 0, max: 0 },
      success_probability: 0.42,
      confidence: 'low',
    }));
    renderWidget();
    await waitFor(() => expect(screen.getByText('$0.000')).toBeInTheDocument());
    expect(screen.getByText('$0.000–$0.000')).toBeInTheDocument();
    // 0.42 -> 42% -> below 60 -> red band
    const pct = screen.getByText('42%');
    expect(pct.className).toContain('text-red-500');
  });

  test('formats a very high cost estimate with an amber success band (60-79%)', async () => {
    estimateGoal.mockResolvedValue(makeEstimate({
      estimated_cost_usd: { min: 5.5, mean: 12.34, max: 40.999 },
      success_probability: 0.65,
    }));
    renderWidget();
    await waitFor(() => expect(screen.getByText('$12.340')).toBeInTheDocument());
    expect(screen.getByText('$5.500–$40.999')).toBeInTheDocument();
    const pct = screen.getByText('65%');
    expect(pct.className).toContain('text-amber-600');
  });

  test('shows a green success band at and above 80%', async () => {
    estimateGoal.mockResolvedValue(makeEstimate({ success_probability: 0.95 }));
    renderWidget();
    await waitFor(() => expect(screen.getByText('95%')).toBeInTheDocument());
    const pct = screen.getByText('95%');
    expect(pct.className).toContain('text-green-600');
  });

  test('renders the step range from estimated_iterations', async () => {
    estimateGoal.mockResolvedValue(makeEstimate({ estimated_iterations: { min: 3, mean: 5, max: 9 } }));
    renderWidget();
    await waitFor(() => expect(screen.getByText('3–9 steps')).toBeInTheDocument());
  });

  test('applies a custom className to the populated container', async () => {
    estimateGoal.mockResolvedValue(makeEstimate());
    const { container } = renderWidget({ className: 'my-widget' });
    await waitFor(() => expect(screen.getByText('Estimated run')).toBeInTheDocument());
    expect(container.querySelector('.my-widget')).toBeInTheDocument();
  });
});
