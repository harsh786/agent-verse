import { render, screen, fireEvent, waitFor } from '@testing-library/react';
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { GoalFeedback } from './GoalFeedback';

// Mock auth store
vi.mock('@/stores/auth', () => ({
  useAuthStore: Object.assign(
    (selector: any) => selector({ apiKey: 'test-api-key' }),
    { getState: () => ({ apiKey: 'test-api-key' }) }
  ),
  getAuthHeader: () => ({ 'X-API-Key': 'test-api-key' }),
}));

const createWrapper = () => {
  const qc = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  return ({ children }: { children: React.ReactNode }) => (
    <QueryClientProvider client={qc}>{children}</QueryClientProvider>
  );
};

describe('GoalFeedback', () => {
  let fetchSpy: ReturnType<typeof vi.fn>;

  beforeEach(() => {
    fetchSpy = vi.fn().mockResolvedValue({
      ok: true,
      json: async () => ({ feedback_id: 'fb-001', status: 'recorded' }),
    });
    vi.stubGlobal('fetch', fetchSpy);
  });

  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it('renders thumbs up and down for complete goals', () => {
    render(<GoalFeedback goalId="g1" status="complete" />, { wrapper: createWrapper() });
    expect(screen.getByLabelText(/thumbs up/i)).toBeInTheDocument();
    expect(screen.getByLabelText(/thumbs down/i)).toBeInTheDocument();
  });

  it('does not render for executing goals', () => {
    const { container } = render(
      <GoalFeedback goalId="g1" status="executing" />,
      { wrapper: createWrapper() }
    );
    expect(container.firstChild).toBeNull();
  });

  it('calls POST /goals/{id}/feedback with rating=1 on thumbs up', async () => {
    render(<GoalFeedback goalId="goal-123" status="complete" />, { wrapper: createWrapper() });
    fireEvent.click(screen.getByLabelText(/thumbs up/i));
    await waitFor(() => {
      expect(fetchSpy).toHaveBeenCalledWith(
        expect.stringContaining('/goals/goal-123/feedback'),
        expect.objectContaining({
          method: 'POST',
          body: expect.stringContaining('"rating":1'),
        })
      );
    });
  });

  it('shows correction textarea after thumbs down click', () => {
    render(<GoalFeedback goalId="g1" status="complete" />, { wrapper: createWrapper() });
    fireEvent.click(screen.getByLabelText(/thumbs down/i));
    expect(screen.getByLabelText(/correction text/i)).toBeInTheDocument();
  });

  it('includes correction text in API call when submitted', async () => {
    render(<GoalFeedback goalId="goal-456" status="failed" />, { wrapper: createWrapper() });
    fireEvent.click(screen.getByLabelText(/thumbs down/i));
    const textarea = screen.getByLabelText(/correction text/i);
    fireEvent.change(textarea, { target: { value: 'The answer should have been X' } });
    fireEvent.click(screen.getByRole('button', { name: /submit/i }));
    await waitFor(() => {
      expect(fetchSpy).toHaveBeenCalledWith(
        expect.stringContaining('/goals/goal-456/feedback'),
        expect.objectContaining({
          body: expect.stringContaining('The answer should have been X'),
        })
      );
    });
  });
});
