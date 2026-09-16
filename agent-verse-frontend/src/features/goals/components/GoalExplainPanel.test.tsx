/**
 * Tests for GoalExplainPanel — the "Why?" tab that fetches
 * GET /goals/:id/explain and renders model selection, execution plan, RAG
 * citations, and decision traces. Branches (loading / error / rich data /
 * no-decision-data / disabled query) are driven through the mocked fetch.
 */
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { render, screen } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, test, vi } from 'vitest';
import { useAuthStore } from '@/stores/auth';
import { GoalExplainPanel } from './GoalExplainPanel';

const FULL = {
  status: 'completed',
  model_selections: { planner: 'claude-planner', executor: 'gpt-exec' },
  plan: ['Gather context', 'Draft the fix', 'Verify'],
  rag_citations: [
    { source_url: 'https://docs.example.com/runbook', content: 'Restart the payments worker when the queue backs up.' },
    { collection_id: 'kb-42' },
  ],
  decision_traces: [
    { action: 'route_to_executor', reasoning: 'Step is a tool call', evidence: ['e1'], confidence: 0.9 },
  ],
};

function mockFetch(payload: unknown, status = 200) {
  return vi.spyOn(globalThis, 'fetch').mockImplementation(async (input) => {
    const url = String(input);
    if (url.includes('/explain'))
      return new Response(JSON.stringify(payload), {
        status,
        headers: { 'Content-Type': 'application/json' },
      });
    return new Response('{}', { status: 200, headers: { 'Content-Type': 'application/json' } });
  });
}

function renderPanel(goalId = 'goal-1') {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={qc}>
      <GoalExplainPanel goalId={goalId} />
    </QueryClientProvider>,
  );
}

beforeEach(() => {
  sessionStorage.clear();
  localStorage.clear();
  useAuthStore.setState({ apiKey: 'k', tenantId: 't', plan: 'free', isAuthenticated: true });
});
afterEach(() => vi.restoreAllMocks());

describe('GoalExplainPanel', () => {
  test('shows a busy loading state while the explanation is in flight', () => {
    vi.spyOn(globalThis, 'fetch').mockReturnValue(new Promise(() => {}) as Promise<Response>);
    renderPanel();
    expect(screen.getByLabelText(/Loading explanation/i)).toHaveAttribute('aria-busy', 'true');
  });

  test('renders all sections for a rich explanation payload', async () => {
    mockFetch(FULL);
    renderPanel();
    expect(await screen.findByTestId('explain-panel')).toBeInTheDocument();
    // Section headings
    expect(screen.getByText(/Model Selection/i)).toBeInTheDocument();
    expect(screen.getByText(/Execution Plan/i)).toBeInTheDocument();
    expect(screen.getByText(/Knowledge Sources Used/i)).toBeInTheDocument();
    expect(screen.getByText(/Decision Traces/i)).toBeInTheDocument();
    // Model selection values + plan steps + citation + trace
    expect(screen.getByText('claude-planner')).toBeInTheDocument();
    expect(screen.getByText('Draft the fix')).toBeInTheDocument();
    expect(screen.getByText('https://docs.example.com/runbook')).toBeInTheDocument();
    // collection_id fallback label when no source_url
    expect(screen.getByText('kb-42')).toBeInTheDocument();
    expect(screen.getByText('route_to_executor')).toBeInTheDocument();
    // Confidence rendered as a rounded percentage
    expect(screen.getByText('90%')).toBeInTheDocument();
  });

  test('renders the error/empty state when the explain request fails', async () => {
    mockFetch({ detail: 'nope' }, 500);
    renderPanel();
    expect(await screen.findByTestId('explain-empty')).toBeInTheDocument();
    expect(screen.getByText(/No explanation available for this goal/i)).toBeInTheDocument();
  });

  test('shows the no-decision-data message when the payload has no recorded decisions', async () => {
    mockFetch({ status: 'completed', model_selections: null, plan: [], rag_citations: [], decision_traces: [] });
    renderPanel();
    expect(await screen.findByText(/No decision data recorded for this goal/i)).toBeInTheDocument();
    // With no sections, none of the section headings render.
    expect(screen.queryByText(/Execution Plan/i)).not.toBeInTheDocument();
  });

  test('renders the empty state (query disabled) when no goalId is provided', async () => {
    const spy = mockFetch(FULL);
    renderPanel('');
    expect(await screen.findByTestId('explain-empty')).toBeInTheDocument();
    // enabled: !!goalId → no explain request fires.
    expect(spy.mock.calls.some(([u]) => String(u).includes('/explain'))).toBe(false);
  });
});
