import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { render, screen, waitFor } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { GoalRunInspector } from './GoalRunInspector';

function renderInspector() {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={qc}>
      <GoalRunInspector goalId="g-123" />
    </QueryClientProvider>
  );
}

const TRACE = {
  goal_id: 'g-123',
  entries: [
    {
      name: 'gen_ai.planner', start_ns: 1, duration_ms: 820, role: 'planner',
      model: 'qwen2.5-72b', input_tokens: 120, output_tokens: 40, cost_usd: 0.0021,
      tool: null, status: 'OK', trace_id: 'a'.repeat(32), span_id: 'b'.repeat(16),
    },
    {
      name: 'agentverse.tool.call', start_ns: 2, duration_ms: 300, role: null,
      model: null, input_tokens: null, output_tokens: null, cost_usd: null,
      tool: 'jira.search', status: 'OK', trace_id: 'a'.repeat(32), span_id: 'c'.repeat(16),
    },
  ],
  summary: {
    steps: 2, generations: 1, total_cost_usd: 0.0021,
    total_input_tokens: 120, total_output_tokens: 40,
  },
};

describe('GoalRunInspector', () => {
  beforeEach(() => {
    vi.spyOn(globalThis, 'fetch').mockResolvedValue(
      new Response(JSON.stringify(TRACE), { status: 200 }) as unknown as Response
    );
  });
  afterEach(() => vi.restoreAllMocks());

  it('renders the timeline with summary, model and tool rows', async () => {
    renderInspector();
    await waitFor(() => expect(screen.getByText('qwen2.5-72b')).toBeInTheDocument());
    // summary tiles
    expect(screen.getByText('LLM calls')).toBeInTheDocument();
    expect(screen.getByText('$0.00210')).toBeInTheDocument();
    // rows: planner generation + jira tool
    expect(screen.getByText('planner')).toBeInTheDocument();
    expect(screen.getByText('jira.search')).toBeInTheDocument();
    expect(screen.getByText('820 ms')).toBeInTheDocument();
    // fetch hit the trace endpoint
    expect(vi.mocked(globalThis.fetch)).toHaveBeenCalledWith(
      expect.stringMatching(/\/observability\/goals\/g-123\/trace$/),
      expect.objectContaining({ headers: expect.any(Object) })
    );
  });

  it('shows an empty state when no entries captured', async () => {
    vi.mocked(globalThis.fetch).mockResolvedValue(
      new Response(JSON.stringify({ ...TRACE, entries: [] }), { status: 200 }) as unknown as Response
    );
    renderInspector();
    await waitFor(() =>
      expect(screen.getByText(/no trace captured yet/i)).toBeInTheDocument()
    );
  });
});
