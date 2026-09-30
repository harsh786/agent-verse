import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { beforeEach, describe, expect, test, vi } from 'vitest';
import { coordinationApi } from './coordinationApi';
import { PatternRunPanel } from './PatternRunPanel';

vi.mock('./coordinationApi', () => ({
  coordinationApi: { runPattern: vi.fn(), submitMagenticReview: vi.fn() },
}));

function renderPanel(onChanged = vi.fn()) {
  const client = new QueryClient({ defaultOptions: { mutations: { retry: false } } });
  render(
    <QueryClientProvider client={client}>
      <PatternRunPanel sessionId="s1" onChanged={onChanged} />
    </QueryClientProvider>,
  );
  return onChanged;
}

beforeEach(() => vi.clearAllMocks());

describe('PatternRunPanel', () => {
  test('runs the selected pattern and shows its outcome', async () => {
    vi.mocked(coordinationApi.runPattern).mockResolvedValue({
      pattern: 'decentralized_swarm', session_id: 's1', execution_id: 'e1', phase: 'completed',
      safe_output: 'SWARM ANSWER', llm_calls: 7,
    });
    const onChanged = renderPanel();
    await userEvent.selectOptions(screen.getByLabelText('Pattern'), 'decentralized_swarm');
    await userEvent.type(screen.getByLabelText('Objective'), 'Map the market');
    await userEvent.click(screen.getByRole('button', { name: /run/i }));
    await waitFor(() => expect(screen.getByText('SWARM ANSWER')).toBeInTheDocument());
    const [session, pattern, objective, key] = vi.mocked(coordinationApi.runPattern).mock.calls[0];
    expect([session, pattern, objective]).toEqual(['s1', 'decentralized_swarm', 'Map the market']);
    expect(key).toBeTruthy();
    expect(onChanged).toHaveBeenCalled();
  });

  test('offers approve/reject when a Magentic run awaits human review', async () => {
    vi.mocked(coordinationApi.runPattern).mockResolvedValue({
      pattern: 'magentic', session_id: 's1', execution_id: 'e1', phase: 'awaiting_human',
      terminal_reason: 'reset_exhausted', llm_calls: 5,
      human_review: { token: 'one-time-token', reason: 'reset_exhausted', submit_path: '/x' },
    });
    vi.mocked(coordinationApi.submitMagenticReview).mockResolvedValue({
      approved: true,
      run: { pattern: 'magentic', session_id: 's1', execution_id: 'e1', phase: 'completed', safe_output: 'FINAL', llm_calls: 9 },
    });
    renderPanel();
    await userEvent.selectOptions(screen.getByLabelText('Pattern'), 'magentic');
    await userEvent.type(screen.getByLabelText('Objective'), 'Write report');
    await userEvent.click(screen.getByRole('button', { name: /run/i }));
    await userEvent.click(await screen.findByRole('button', { name: /approve another replan/i }));
    expect(coordinationApi.submitMagenticReview).toHaveBeenCalledWith('s1', 'one-time-token', true);
    await waitFor(() => expect(screen.getByText('FINAL')).toBeInTheDocument());
  });
});
