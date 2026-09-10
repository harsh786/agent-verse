import { render, screen, waitFor } from '@testing-library/react';
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import type { EvalSuggestions } from '@/lib/api/client';
import { EvalSuggestionsPanel } from './EvalSuggestionsPanel';

const getEvalSuggestions = vi.fn();
vi.mock('@/lib/api/client', () => ({
  goalsApi: { getEvalSuggestions: (id: string) => getEvalSuggestions(id) },
}));

function renderPanel(enabled = true) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={qc}>
      <EvalSuggestionsPanel goalId="g1" enabled={enabled} />
    </QueryClientProvider>,
  );
}

beforeEach(() => getEvalSuggestions.mockReset());

describe('EvalSuggestionsPanel', () => {
  it('renders real suggestions for sub-threshold dimensions (worst first)', async () => {
    const data: EvalSuggestions = {
      goal_id: 'g1',
      status: 'evaluated',
      pass_threshold: 0.6,
      count: 2,
      suggestions: [
        { dimension: 'accuracy', score: 0.3, threshold: 0.6, suggestion: 'Attach a knowledge collection (RAG).' },
        { dimension: 'sla', score: 0.45, threshold: 0.6, suggestion: 'Cache retrieval or use a faster model.' },
      ],
    };
    getEvalSuggestions.mockResolvedValue(data);
    renderPanel();

    expect(await screen.findByText(/improvement suggestions/i)).toBeInTheDocument();
    expect(screen.getByText(/attach a knowledge collection/i)).toBeInTheDocument();
    expect(screen.getByText(/cache retrieval/i)).toBeInTheDocument();
    // The real score is shown, not fabricated.
    expect(screen.getByText('30%')).toBeInTheDocument();
  });

  it('shows an honest all-passed state when there are no suggestions', async () => {
    getEvalSuggestions.mockResolvedValue({
      goal_id: 'g1', status: 'evaluated', pass_threshold: 0.6, count: 0, suggestions: [],
    } satisfies EvalSuggestions);
    renderPanel();
    expect(await screen.findByText(/every dimension passed/i)).toBeInTheDocument();
  });

  it('renders nothing when disabled', () => {
    const { container } = renderPanel(false);
    expect(container).toBeEmptyDOMElement();
    expect(getEvalSuggestions).not.toHaveBeenCalled();
  });

  it('renders nothing for an unevaluated goal', async () => {
    getEvalSuggestions.mockResolvedValue({
      goal_id: 'g1', status: 'not_evaluated', pass_threshold: null, count: 0, suggestions: [],
    } satisfies EvalSuggestions);
    const { container } = renderPanel();
    await waitFor(() => expect(getEvalSuggestions).toHaveBeenCalled());
    expect(container.querySelector('section')).toBeNull();
  });
});
