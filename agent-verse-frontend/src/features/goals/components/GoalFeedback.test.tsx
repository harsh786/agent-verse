import { render, screen, fireEvent } from '@testing-library/react';
import { describe, it, expect, vi } from 'vitest';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { GoalFeedback } from './GoalFeedback';

vi.mock('@/stores/auth', () => ({ useAuthStore: { getState: () => ({ apiKey: 'test-key' }) } }));

const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
const wrap = (node: React.ReactNode) => <QueryClientProvider client={qc}>{node}</QueryClientProvider>;

describe('GoalFeedback', () => {
  it('renders thumbs up and down buttons for complete goals', () => {
    render(wrap(<GoalFeedback goalId="g1" status="complete" />));
    expect(screen.getByLabelText(/thumbs up/i)).toBeInTheDocument();
    expect(screen.getByLabelText(/thumbs down/i)).toBeInTheDocument();
  });

  it('does not render for executing goals', () => {
    const { container } = render(wrap(<GoalFeedback goalId="g1" status="executing" />));
    expect(container.firstChild).toBeNull();
  });

  it('shows correction textarea on thumbs down click', async () => {
    render(wrap(<GoalFeedback goalId="g1" status="complete" />));
    fireEvent.click(screen.getByLabelText(/thumbs down/i));
    expect(screen.getByLabelText(/correction text/i)).toBeInTheDocument();
  });
});
