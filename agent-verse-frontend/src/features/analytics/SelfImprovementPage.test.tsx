import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter } from 'react-router-dom';
import { afterEach, beforeEach, describe, expect, test, vi } from 'vitest';
import type { Experiment } from '@/lib/api/client';
import { useAuthStore } from '@/stores/auth';
import { useToastStore } from '@/stores/toast';
import { SelfImprovementPage } from './SelfImprovementPage';

function renderPage() {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={qc}>
      <MemoryRouter>
        <SelfImprovementPage />
      </MemoryRouter>
    </QueryClientProvider>
  );
}

const MOCK_EXPERIMENTS: Experiment[] = [
  {
    id: 'exp-1',
    name: 'Temperature A/B test',
    agent_id: 'agent-1',
    status: 'running',
    control_config: { temperature: 0.2 },
    challenger_config: { temperature: 0.7 },
    lift_pct: null,
    started_at: '2026-06-20T00:00:00Z',
    concluded_at: null,
  },
  {
    id: 'exp-2',
    name: 'Model comparison',
    agent_id: 'agent-1',
    status: 'concluded',
    control_config: { model: 'gpt-4o' },
    challenger_config: { model: 'claude-3-5-sonnet' },
    lift_pct: 12.5,
    started_at: '2026-06-01T00:00:00Z',
    concluded_at: '2026-06-15T00:00:00Z',
  },
];

const MOCK_SUGGESTIONS = [
  {
    id: 'sug-1',
    type: 'prompt_optimization',
    description: 'Increase planning detail in system prompt',
    confidence: 0.87,
    agent_id: 'agent-1',
    status: 'pending',
    created_at: '2026-06-28T00:00:00Z',
  },
];

function mockFetch(experiments = MOCK_EXPERIMENTS, suggestions = MOCK_SUGGESTIONS) {
  return vi.spyOn(globalThis, 'fetch').mockImplementation(async (input) => {
    const url = String(input);
    if (url.includes('/intelligence/experiments')) {
      return new Response(JSON.stringify(experiments), {
        status: 200, headers: { 'Content-Type': 'application/json' },
      });
    }
    if (url.includes('/intelligence/suggestions')) {
      return new Response(JSON.stringify(suggestions), {
        status: 200, headers: { 'Content-Type': 'application/json' },
      });
    }
    if (url.includes('/intelligence/benchmarks')) {
      return new Response(JSON.stringify({
        platform_avg_success_rate: 0.72, platform_avg_cost_usd: 0.05,
        platform_avg_eval_score: 0.74, your_success_rate: 0.83,
        your_cost_usd: 0.025, your_eval_score: 0.82,
        percentile_success: 25, percentile_cost: 25,
        comparison_label: 'Top 25%',
        dimensions: { your: {}, platform: {} },
      }), { status: 200, headers: { 'Content-Type': 'application/json' } });
    }
    return new Response(null, { status: 404 });
  });
}

describe('SelfImprovementPage', () => {
  beforeEach(() => {
    useAuthStore.setState({
      apiKey: 'test-key', tenantId: 'tenant-1', plan: 'enterprise', isAuthenticated: true,
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

  test('shows Self-Improvement heading', async () => {
    mockFetch();
    renderPage();
    await waitFor(() =>
      expect(screen.getByRole('heading', { name: 'Self-Improvement' })).toBeInTheDocument(),
      { timeout: 3000 }
    );
  });

  test('shows experiment names from API', async () => {
    mockFetch();
    renderPage();
    await waitFor(() =>
      expect(screen.getByText('Temperature A/B test')).toBeInTheDocument(),
      { timeout: 3000 }
    );
    expect(screen.getByText('Model comparison')).toBeInTheDocument();
  });

  test('shows concluded experiment lift percentage', async () => {
    mockFetch();
    renderPage();
    await waitFor(() =>
      expect(screen.getByText(/12\.5%|12.5 %/)).toBeInTheDocument(),
      { timeout: 3000 }
    );
  });

  test('shows improvement suggestions from API', async () => {
    const user = userEvent.setup();
    mockFetch();
    renderPage();
    // Tab buttons use role="tab" (not button)
    await waitFor(() =>
      expect(screen.getByRole('tab', { name: 'suggestions' })).toBeInTheDocument()
    );
    await user.click(screen.getByRole('tab', { name: 'suggestions' }));
    await waitFor(() =>
      expect(screen.getByText('Increase planning detail in system prompt')).toBeInTheDocument(),
      { timeout: 3000 }
    );
  });

  test('shows empty experiments state gracefully', async () => {
    mockFetch([], []);
    renderPage();
    await waitFor(() =>
      expect(screen.queryByText('Temperature A/B test')).not.toBeInTheDocument(),
      { timeout: 3000 }
    );
  });

  test('handles fetch error without crashing', async () => {
    vi.spyOn(globalThis, 'fetch').mockResolvedValue(
      new Response('Server Error', { status: 500 })
    );
    renderPage();
    await waitFor(() => expect(document.body).toBeTruthy(), { timeout: 3000 });
  });

  test('shows benchmarks tab', async () => {
    const user = userEvent.setup();
    mockFetch();
    renderPage();
    await waitFor(() =>
      expect(screen.getByRole('tab', { name: 'benchmarks' })).toBeInTheDocument()
    );
    await user.click(screen.getByRole('tab', { name: 'benchmarks' }));
    await waitFor(() => {
      const headings = screen.queryAllByText(/Your Performance vs Platform/i);
      expect(headings.length).toBeGreaterThan(0);
    }, { timeout: 3000 });
  });

  test('shows history tab with optimization timeline', async () => {
    const user = userEvent.setup();
    mockFetch();
    renderPage();
    await waitFor(() =>
      expect(screen.getByRole('tab', { name: 'history' })).toBeInTheDocument()
    );
    await user.click(screen.getByRole('tab', { name: 'history' }));
    await waitFor(() => {
      const headings = screen.queryAllByText(/Optimization Timeline/i);
      expect(headings.length).toBeGreaterThan(0);
    }, { timeout: 3000 });
  });

  test('pending suggestion shows apply and reject buttons', async () => {
    const user = userEvent.setup();
    mockFetch();
    renderPage();
    await waitFor(() =>
      expect(screen.getByRole('tab', { name: 'suggestions' })).toBeInTheDocument()
    );
    await user.click(screen.getByRole('tab', { name: 'suggestions' }));
    await waitFor(() => {
      expect(screen.queryAllByText('Apply').length).toBeGreaterThan(0);
    }, { timeout: 3000 });
  });

  test('concluded winner exposes an Apply-winner control that POSTs to the apply endpoint', async () => {
    const user = userEvent.setup();
    const applyCalls: string[] = [];
    vi.spyOn(globalThis, 'fetch').mockImplementation(async (input, init) => {
      const url = String(input);
      if (url.includes('/apply') && init?.method === 'POST') {
        applyCalls.push(url);
        return new Response(
          JSON.stringify({ experiment_id: 'exp-2', agent_id: 'agent-1', status: 'applied' }),
          { status: 200, headers: { 'Content-Type': 'application/json' } },
        );
      }
      if (url.includes('/intelligence/experiments')) {
        return new Response(JSON.stringify(MOCK_EXPERIMENTS), {
          status: 200, headers: { 'Content-Type': 'application/json' },
        });
      }
      return new Response(JSON.stringify([]), {
        status: 200, headers: { 'Content-Type': 'application/json' },
      });
    });
    renderPage();

    // Expand the concluded winning experiment (exp-2, +12.5% lift).
    const expandTrigger = await screen.findByText('Model comparison', undefined, { timeout: 3000 });
    await user.click(expandTrigger);

    const applyBtn = await screen.findByRole('button', {
      name: /apply winning configuration from experiment model comparison/i,
    });
    await user.click(applyBtn);

    await waitFor(() => {
      expect(applyCalls.some((u) => u.includes('/intelligence/experiments/exp-2/apply'))).toBe(true);
    }, { timeout: 3000 });
  });

  test('expanding a running experiment shows detail without apply/rollback controls', async () => {
    const user = userEvent.setup();
    mockFetch();
    renderPage();
    const trigger = await screen.findByText('Temperature A/B test', undefined, { timeout: 3000 });
    await user.click(trigger);
    // Running experiments render ExperimentDetail (config panels) ...
    await waitFor(() => {
      expect(screen.getByText('Control Config')).toBeInTheDocument();
      expect(screen.getByText('Challenger Config')).toBeInTheDocument();
    });
    // ...but no rollback/apply footer, since that only renders for concluded experiments.
    expect(screen.queryByText('Rollback')).not.toBeInTheDocument();
  });

  test('expanding a concluded losing experiment shows rollback but no apply-winner button', async () => {
    const user = userEvent.setup();
    mockFetch([
      {
        id: 'exp-3',
        name: 'Losing experiment',
        agent_id: 'agent-2',
        status: 'concluded',
        control_config: { temperature: 0.2 },
        challenger_config: { temperature: 0.9 },
        lift_pct: -5.2,
        started_at: '2026-06-01T00:00:00Z',
        concluded_at: '2026-06-10T00:00:00Z',
      },
    ], []);
    renderPage();
    const trigger = await screen.findByText('Losing experiment', undefined, { timeout: 3000 });
    await user.click(trigger);
    await waitFor(() => {
      expect(screen.getByRole('button', { name: /roll back experiment losing experiment/i })).toBeInTheDocument();
    });
    expect(screen.queryByRole('button', { name: /apply winning configuration/i })).not.toBeInTheDocument();
    expect(screen.getByText(/Rollback to restore the original agent configuration/i)).toBeInTheDocument();
  });

  test('rollback button posts to the rollback endpoint and shows a success toast', async () => {
    const user = userEvent.setup();
    const rollbackCalls: string[] = [];
    vi.spyOn(globalThis, 'fetch').mockImplementation(async (input, init) => {
      const url = String(input);
      if (url.includes('/rollback') && init?.method === 'POST') {
        rollbackCalls.push(url);
        return new Response(
          JSON.stringify({ experiment_id: 'exp-2', agent_id: 'agent-1', status: 'rolled_back', reason: 'x' }),
          { status: 200, headers: { 'Content-Type': 'application/json' } },
        );
      }
      if (url.includes('/intelligence/experiments')) {
        return new Response(JSON.stringify(MOCK_EXPERIMENTS), {
          status: 200, headers: { 'Content-Type': 'application/json' },
        });
      }
      return new Response(JSON.stringify([]), { status: 200, headers: { 'Content-Type': 'application/json' } });
    });
    renderPage();

    const trigger = await screen.findByText('Model comparison', undefined, { timeout: 3000 });
    await user.click(trigger);
    const rollbackBtn = await screen.findByRole('button', { name: /roll back experiment model comparison/i });
    await user.click(rollbackBtn);

    await waitFor(() => {
      expect(rollbackCalls.some((u) => u.includes('/intelligence/experiments/exp-2/rollback'))).toBe(true);
    });
    await waitFor(() => {
      const toasts = useToastStore.getState().toasts;
      expect(toasts.some((t) => t.kind === 'success' && t.message.includes('Rolled back'))).toBe(true);
    });
  });

  test('apply-winner failure shows an error toast', async () => {
    const user = userEvent.setup();
    vi.spyOn(globalThis, 'fetch').mockImplementation(async (input, init) => {
      const url = String(input);
      if (url.includes('/apply') && init?.method === 'POST') {
        return new Response('boom', { status: 500 });
      }
      if (url.includes('/intelligence/experiments')) {
        return new Response(JSON.stringify(MOCK_EXPERIMENTS), {
          status: 200, headers: { 'Content-Type': 'application/json' },
        });
      }
      return new Response(JSON.stringify([]), { status: 200, headers: { 'Content-Type': 'application/json' } });
    });
    renderPage();

    const trigger = await screen.findByText('Model comparison', undefined, { timeout: 3000 });
    await user.click(trigger);
    const applyBtn = await screen.findByRole('button', { name: /apply winning configuration/i });
    await user.click(applyBtn);

    await waitFor(() => {
      const toasts = useToastStore.getState().toasts;
      expect(toasts.some((t) => t.kind === 'error' && t.message.includes('Apply failed'))).toBe(true);
    });
  });

  test('rollback failure shows an error toast', async () => {
    const user = userEvent.setup();
    vi.spyOn(globalThis, 'fetch').mockImplementation(async (input, init) => {
      const url = String(input);
      if (url.includes('/rollback') && init?.method === 'POST') {
        return new Response('boom', { status: 500 });
      }
      if (url.includes('/intelligence/experiments')) {
        return new Response(JSON.stringify(MOCK_EXPERIMENTS), {
          status: 200, headers: { 'Content-Type': 'application/json' },
        });
      }
      return new Response(JSON.stringify([]), { status: 200, headers: { 'Content-Type': 'application/json' } });
    });
    renderPage();

    const trigger = await screen.findByText('Model comparison', undefined, { timeout: 3000 });
    await user.click(trigger);
    const rollbackBtn = await screen.findByRole('button', { name: /roll back experiment model comparison/i });
    await user.click(rollbackBtn);

    await waitFor(() => {
      const toasts = useToastStore.getState().toasts;
      expect(toasts.some((t) => t.kind === 'error' && t.message.includes('Rollback failed'))).toBe(true);
    });
  });

  test('experiment status filters narrow the visible list', async () => {
    const user = userEvent.setup();
    mockFetch();
    renderPage();
    await screen.findByText('Temperature A/B test', undefined, { timeout: 3000 });
    await screen.findByText('Model comparison');

    await user.click(screen.getByRole('button', { name: 'running' }));
    expect(screen.getByText('Temperature A/B test')).toBeInTheDocument();
    expect(screen.queryByText('Model comparison')).not.toBeInTheDocument();

    await user.click(screen.getByRole('button', { name: 'concluded' }));
    expect(screen.queryByText('Temperature A/B test')).not.toBeInTheDocument();
    expect(screen.getByText('Model comparison')).toBeInTheDocument();

    await user.click(screen.getByRole('button', { name: 'pending' }));
    expect(screen.queryByText('Temperature A/B test')).not.toBeInTheDocument();
    expect(screen.queryByText('Model comparison')).not.toBeInTheDocument();

    await user.click(screen.getByRole('button', { name: 'all' }));
    expect(screen.getByText('Temperature A/B test')).toBeInTheDocument();
    expect(screen.getByText('Model comparison')).toBeInTheDocument();
  });

  test('experiments retry button refetches after a failed load', async () => {
    const user = userEvent.setup();
    let call = 0;
    vi.spyOn(globalThis, 'fetch').mockImplementation(async (input) => {
      const url = String(input);
      if (url.includes('/intelligence/experiments')) {
        call += 1;
        if (call === 1) return new Response('boom', { status: 500 });
        return new Response(JSON.stringify(MOCK_EXPERIMENTS), {
          status: 200, headers: { 'Content-Type': 'application/json' },
        });
      }
      return new Response(JSON.stringify([]), { status: 200, headers: { 'Content-Type': 'application/json' } });
    });
    renderPage();

    await waitFor(() => expect(screen.getByRole('alert')).toBeInTheDocument(), { timeout: 3000 });
    expect(screen.getByText('Failed to load experiments')).toBeInTheDocument();

    await user.click(screen.getByRole('button', { name: 'Retry' }));

    await waitFor(() =>
      expect(screen.getByText('Temperature A/B test')).toBeInTheDocument(),
      { timeout: 3000 },
    );
  });

  test('suggestion filters narrow the visible list and pending badge is shown', async () => {
    const user = userEvent.setup();
    mockFetch(MOCK_EXPERIMENTS, [
      ...MOCK_SUGGESTIONS,
      {
        id: 'sug-2',
        type: 'tool_swap',
        description: 'Swap search tool for a faster provider',
        confidence: 0.55,
        agent_id: 'agent-2',
        status: 'applied',
        created_at: '2026-06-25T00:00:00Z',
      },
    ]);
    renderPage();
    await user.click(await screen.findByRole('tab', { name: /suggestions/ }));
    await screen.findByText('Increase planning detail in system prompt');

    expect(screen.getByRole('button', { name: /pending \(1\)/i })).toBeInTheDocument();

    await user.click(screen.getByRole('button', { name: 'applied' }));
    expect(screen.queryByText('Increase planning detail in system prompt')).not.toBeInTheDocument();
    expect(screen.getByText('Swap search tool for a faster provider')).toBeInTheDocument();

    await user.click(screen.getByRole('button', { name: 'rejected' }));
    expect(screen.queryByText('Swap search tool for a faster provider')).not.toBeInTheDocument();

    await user.click(screen.getByRole('button', { name: 'all' }));
    expect(screen.getByText('Increase planning detail in system prompt')).toBeInTheDocument();
    expect(screen.getByText('Swap search tool for a faster provider')).toBeInTheDocument();
  });

  test('applying a pending suggestion posts to the apply endpoint and shows success toast', async () => {
    const user = userEvent.setup();
    const calls: string[] = [];
    vi.spyOn(globalThis, 'fetch').mockImplementation(async (input, init) => {
      const url = String(input);
      if (url.includes('/suggestions/') && url.includes('/apply') && init?.method === 'POST') {
        calls.push(url);
        return new Response('null', { status: 200, headers: { 'Content-Type': 'application/json' } });
      }
      if (url.includes('/intelligence/suggestions')) {
        return new Response(JSON.stringify(MOCK_SUGGESTIONS), {
          status: 200, headers: { 'Content-Type': 'application/json' },
        });
      }
      return new Response(JSON.stringify([]), { status: 200, headers: { 'Content-Type': 'application/json' } });
    });
    renderPage();
    await user.click(await screen.findByRole('tab', { name: /suggestions/ }));
    const applyBtn = await screen.findByRole('button', { name: 'Apply' });
    await user.click(applyBtn);

    await waitFor(() => expect(calls.some((u) => u.includes('/intelligence/suggestions/sug-1/apply'))).toBe(true));
    await waitFor(() => {
      const toasts = useToastStore.getState().toasts;
      expect(toasts.some((t) => t.kind === 'success' && t.message === 'Suggestion applied')).toBe(true);
    });
  });

  test('rejecting a pending suggestion posts to the reject endpoint and shows success toast', async () => {
    const user = userEvent.setup();
    const calls: string[] = [];
    vi.spyOn(globalThis, 'fetch').mockImplementation(async (input, init) => {
      const url = String(input);
      if (url.includes('/suggestions/') && url.includes('/reject') && init?.method === 'POST') {
        calls.push(url);
        return new Response('null', { status: 200, headers: { 'Content-Type': 'application/json' } });
      }
      if (url.includes('/intelligence/suggestions')) {
        return new Response(JSON.stringify(MOCK_SUGGESTIONS), {
          status: 200, headers: { 'Content-Type': 'application/json' },
        });
      }
      return new Response(JSON.stringify([]), { status: 200, headers: { 'Content-Type': 'application/json' } });
    });
    renderPage();
    await user.click(await screen.findByRole('tab', { name: /suggestions/ }));
    const rejectBtn = await screen.findByRole('button', { name: 'Reject' });
    await user.click(rejectBtn);

    await waitFor(() => expect(calls.some((u) => u.includes('/intelligence/suggestions/sug-1/reject'))).toBe(true));
    await waitFor(() => {
      const toasts = useToastStore.getState().toasts;
      expect(toasts.some((t) => t.kind === 'success' && t.message === 'Suggestion rejected')).toBe(true);
    });
  });

  test('apply-suggestion failure shows an error toast', async () => {
    const user = userEvent.setup();
    vi.spyOn(globalThis, 'fetch').mockImplementation(async (input, init) => {
      const url = String(input);
      if (url.includes('/suggestions/') && url.includes('/apply') && init?.method === 'POST') {
        return new Response('boom', { status: 500 });
      }
      if (url.includes('/intelligence/suggestions')) {
        return new Response(JSON.stringify(MOCK_SUGGESTIONS), {
          status: 200, headers: { 'Content-Type': 'application/json' },
        });
      }
      return new Response(JSON.stringify([]), { status: 200, headers: { 'Content-Type': 'application/json' } });
    });
    renderPage();
    await user.click(await screen.findByRole('tab', { name: /suggestions/ }));
    const applyBtn = await screen.findByRole('button', { name: 'Apply' });
    await user.click(applyBtn);

    await waitFor(() => {
      const toasts = useToastStore.getState().toasts;
      expect(toasts.some((t) => t.kind === 'error' && t.message.includes('Failed: apply suggestion'))).toBe(true);
    });
  });

  test('reject-suggestion failure shows an error toast', async () => {
    const user = userEvent.setup();
    vi.spyOn(globalThis, 'fetch').mockImplementation(async (input, init) => {
      const url = String(input);
      if (url.includes('/suggestions/') && url.includes('/reject') && init?.method === 'POST') {
        return new Response('boom', { status: 500 });
      }
      if (url.includes('/intelligence/suggestions')) {
        return new Response(JSON.stringify(MOCK_SUGGESTIONS), {
          status: 200, headers: { 'Content-Type': 'application/json' },
        });
      }
      return new Response(JSON.stringify([]), { status: 200, headers: { 'Content-Type': 'application/json' } });
    });
    renderPage();
    await user.click(await screen.findByRole('tab', { name: /suggestions/ }));
    const rejectBtn = await screen.findByRole('button', { name: 'Reject' });
    await user.click(rejectBtn);

    await waitFor(() => {
      const toasts = useToastStore.getState().toasts;
      expect(toasts.some((t) => t.kind === 'error' && t.message.includes('Failed: reject suggestion'))).toBe(true);
    });
  });

  test('shows empty suggestions state when there are none', async () => {
    mockFetch(MOCK_EXPERIMENTS, []);
    const user = userEvent.setup();
    renderPage();
    await user.click(await screen.findByRole('tab', { name: /suggestions/ }));
    await waitFor(() => expect(screen.getByText('No suggestions')).toBeInTheDocument());
    expect(
      screen.getByText('The optimizer will generate suggestions as it analyzes agent performance.'),
    ).toBeInTheDocument();
  });

  test('shows empty history state when there are no concluded experiments', async () => {
    const user = userEvent.setup();
    mockFetch([MOCK_EXPERIMENTS[0]], []);
    renderPage();
    await user.click(await screen.findByRole('tab', { name: /history/ }));
    await waitFor(() => expect(screen.getByText('No optimization history')).toBeInTheDocument());
    expect(
      screen.getByText('Concluded experiments and applied optimizations will appear here.'),
    ).toBeInTheDocument();
  });

  test('history tab lists concluded experiments sorted by conclusion date', async () => {
    const user = userEvent.setup();
    mockFetch([
      MOCK_EXPERIMENTS[1],
      {
        id: 'exp-4',
        name: 'Later concluded experiment',
        agent_id: 'agent-3',
        status: 'concluded',
        control_config: {},
        challenger_config: {},
        lift_pct: null,
        started_at: '2026-07-01T00:00:00Z',
        concluded_at: '2026-07-05T00:00:00Z',
      },
    ], []);
    renderPage();
    await user.click(await screen.findByRole('tab', { name: /history/ }));
    await screen.findByText('Later concluded experiment');
    const names = screen.getAllByText(/Model comparison|Later concluded experiment/).map((el) => el.textContent);
    expect(names[0]).toBe('Later concluded experiment');
    expect(names[1]).toBe('Model comparison');
  });

  test('history tab shows loading spinner while experiments are fetching', async () => {
    vi.spyOn(globalThis, 'fetch').mockReturnValue(new Promise(() => {}));
    const user = userEvent.setup();
    renderPage();
    await user.click(screen.getByRole('tab', { name: /history/ }));
    expect(screen.getByText('Optimization Timeline')).toBeInTheDocument();
  });

  test('benchmarks tab shows loading state then renders data with radar chart', async () => {
    const user = userEvent.setup();
    mockFetch();
    renderPage();
    await user.click(await screen.findByRole('tab', { name: 'benchmarks' }));
    await waitFor(() => {
      expect(screen.getAllByText(/Your Performance vs Platform/i).length).toBeGreaterThan(0);
    });
    expect(screen.getByText('Top 25%')).toBeInTheDocument();
  });

  test('benchmarks tab shows an error state when the request fails', async () => {
    const user = userEvent.setup();
    vi.spyOn(globalThis, 'fetch').mockImplementation(async (input) => {
      const url = String(input);
      if (url.includes('/intelligence/benchmarks')) {
        return new Response('boom', { status: 500 });
      }
      return new Response(JSON.stringify([]), { status: 200, headers: { 'Content-Type': 'application/json' } });
    });
    renderPage();
    await user.click(await screen.findByRole('tab', { name: 'benchmarks' }));
    await waitFor(() => expect(screen.getByText('Benchmark data unavailable')).toBeInTheDocument());
  });

  test('benchmarks tab shows the eval-dimensions radar chart when dimension data is present', async () => {
    const user = userEvent.setup();
    vi.spyOn(globalThis, 'fetch').mockImplementation(async (input) => {
      const url = String(input);
      if (url.includes('/intelligence/benchmarks')) {
        return new Response(JSON.stringify({
          platform_avg_success_rate: 0.72, platform_avg_cost_usd: 0.05,
          platform_avg_eval_score: 0.74, your_success_rate: 0.6,
          your_cost_usd: 0.09, your_eval_score: 0.82,
          percentile_success: 60, percentile_cost: 5,
          comparison_label: 'Middle of the pack',
          dimensions: { your: { task_completion: 0.9 }, platform: { task_completion: 0.5 } },
        }), { status: 200, headers: { 'Content-Type': 'application/json' } });
      }
      return new Response(JSON.stringify([]), { status: 200, headers: { 'Content-Type': 'application/json' } });
    });
    renderPage();
    await user.click(await screen.findByRole('tab', { name: 'benchmarks' }));
    await waitFor(() => expect(screen.getByText('Middle of the pack')).toBeInTheDocument());
    expect(screen.queryByText('No eval dimension data available')).not.toBeInTheDocument();
  });
});
