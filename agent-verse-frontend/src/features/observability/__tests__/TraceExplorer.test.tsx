import { describe, it, expect, beforeEach, afterEach, vi } from 'vitest';
import { render, screen, waitFor } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { useAuthStore } from '@/stores/auth';
import { TraceExplorer } from '../TraceExplorer';

function renderExplorer() {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(<QueryClientProvider client={qc}><TraceExplorer /></QueryClientProvider>);
}

beforeEach(() => {
  useAuthStore.setState({ apiKey: '', tenantId: '', plan: 'free', isAuthenticated: false });
});
afterEach(() => vi.restoreAllMocks());

describe('TraceExplorer', () => {
  it('renders without crashing', () => {
    renderExplorer();
  });

  it('renders retrieved-chunk scores and grounding attribution from a sample attributes payload', async () => {
    useAuthStore.setState({ apiKey: 'test-key', tenantId: 't1', plan: 'free', isAuthenticated: true });
    vi.spyOn(globalThis, 'fetch').mockResolvedValue(new Response(JSON.stringify({
      traces: [{
        trace_id: 't1', goal_id: 'g1', goal: 'Answer from the knowledge base',
        total_cost_usd: 0.002, total_tokens: 120, created_at: '2026-09-01T00:00:00Z',
        spans: [
          {
            span_id: 's1', name: 'rag.search', start_time: 0, duration_ms: 42, status: 'ok',
            attributes: {
              retrieved_chunks: JSON.stringify([
                { chunk_id: 'c1', score: 0.91, text: 'AgentVerse routes goals through a LangGraph state machine.' },
                { chunk_id: 'c2', score: 0.54, text: 'Row-level security enforces tenant isolation at the DB layer.' },
              ]),
              grounded_chunk_id: 'c1',
              collection_id: 'col-1',
            },
          },
        ],
      }],
    }), { status: 200, headers: { 'Content-Type': 'application/json' } }));

    renderExplorer();

    expect(await screen.findByText(/routes goals through a LangGraph/)).toBeInTheDocument();
    expect(screen.getByText(/tenant isolation/)).toBeInTheDocument();
    expect(screen.getByText('91%')).toBeInTheDocument();
    expect(screen.getByText('54%')).toBeInTheDocument();
    expect(screen.getByText('Grounded')).toBeInTheDocument();
    // Non-structured attributes still surface as plain key/value pills.
    expect(screen.getByText(/collection_id: col-1/)).toBeInTheDocument();
  });

  it('renders nothing extra for a span with no attributes (no fabrication)', async () => {
    useAuthStore.setState({ apiKey: 'test-key', tenantId: 't1', plan: 'free', isAuthenticated: true });
    vi.spyOn(globalThis, 'fetch').mockResolvedValue(new Response(JSON.stringify({
      traces: [{
        trace_id: 't2', goal_id: 'g2', goal: 'Plain goal',
        total_cost_usd: 0.001, total_tokens: 40, created_at: '2026-09-01T00:00:00Z',
        spans: [
          { span_id: 's2', name: 'llm.planner', start_time: 0, duration_ms: 10, status: 'ok', attributes: {} },
        ],
      }],
    }), { status: 200, headers: { 'Content-Type': 'application/json' } }));

    renderExplorer();

    await waitFor(() => expect(screen.getByText('llm.planner')).toBeInTheDocument());
    expect(screen.queryByTestId('span-attributes')).not.toBeInTheDocument();
    expect(screen.queryByLabelText('Retrieved chunks')).not.toBeInTheDocument();
  });
});
