import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter } from 'react-router-dom';
import { afterEach, beforeEach, describe, expect, test, vi } from 'vitest';
import { useAuthStore } from '@/stores/auth';
import { ApprovalsPage } from './ApprovalsPage';

// Mock useEventStream so SSE doesn't interfere with fetch mocks
vi.mock('@/lib/sse/useEventStream', () => ({
  useEventStream: (_path: string | null, opts?: { onEvent?: (e: { type: string }) => void }) => {
    // Simulate a pushed event after mount in a controlled way
    setTimeout(() => opts?.onEvent?.({ type: 'waiting_approval' }), 0);
    return { events: [], connected: true };
  },
}));

const PENDING_APPROVAL = {
  request_id: 'req-001',
  goal_id: 'goal-abc',
  action: 'delete_file /critical/path',
  risk_level: 'high',
  status: 'pending',
};

function renderPage() {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  return render(
    <MemoryRouter>
      <QueryClientProvider client={queryClient}>
        <ApprovalsPage />
      </QueryClientProvider>
    </MemoryRouter>
  );
}

let invalidateSpy: ReturnType<typeof vi.fn>;

function renderPageWithSpy() {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  invalidateSpy = vi.spyOn(qc, 'invalidateQueries') as unknown as ReturnType<typeof vi.fn>;
  return render(
    <QueryClientProvider client={qc}>
      <MemoryRouter><ApprovalsPage /></MemoryRouter>
    </QueryClientProvider>,
  );
}

describe('ApprovalsPage', () => {
  beforeEach(() => {
    useAuthStore.setState({
      apiKey: 'test-key',
      tenantId: 'tenant-1',
      isAuthenticated: true,
    });
  });

  afterEach(() => {
    vi.restoreAllMocks();
  });

  test('renders "Approval Inbox" heading', () => {
    vi.spyOn(globalThis, 'fetch').mockReturnValue(new Promise(() => {}));
    renderPage();
    expect(screen.getByText('Approval Inbox')).toBeInTheDocument();
  });

  test('shows pending count badge when approvals exist', async () => {
    vi.spyOn(globalThis, 'fetch').mockResolvedValue({
      ok: true,
      status: 200,
      json: async () => [PENDING_APPROVAL],
    } as Response);

    renderPage();

    await waitFor(() => {
      expect(screen.getByText('1')).toBeInTheDocument();
    });
  });

  test('shows empty state when no pending approvals', async () => {
    vi.spyOn(globalThis, 'fetch').mockResolvedValue({
      ok: true,
      status: 200,
      json: async () => [],
    } as Response);

    renderPage();

    await waitFor(() => {
      expect(screen.getByTestId('empty-state')).toBeInTheDocument();
    });
    expect(screen.getByText('No pending approval requests.')).toBeInTheDocument();
  });

  test('approve button calls approve API', async () => {
    const user = userEvent.setup();
    let callCount = 0;
    vi.spyOn(globalThis, 'fetch').mockImplementation(async (url) => {
      callCount++;
      if (String(url).includes('/approve')) {
        return { ok: true, status: 200, json: async () => ({ status: 'approved' }) } as Response;
      }
      return { ok: true, status: 200, json: async () => [PENDING_APPROVAL] } as Response;
    });

    renderPage();

    await waitFor(() => screen.getByText('Approve'));
    await user.click(screen.getByText('Approve'));

    await waitFor(() => {
      expect(callCount).toBeGreaterThan(1);
    });
  });

  test('reject button calls reject API', async () => {
    const user = userEvent.setup();
    let rejectCalled = false;
    vi.spyOn(globalThis, 'fetch').mockImplementation(async (url) => {
      if (String(url).includes('/reject')) {
        rejectCalled = true;
        return { ok: true, status: 200, json: async () => ({ status: 'rejected' }) } as Response;
      }
      return { ok: true, status: 200, json: async () => [PENDING_APPROVAL] } as Response;
    });

    renderPage();

    await waitFor(() => screen.getByText('Reject'));
    await user.click(screen.getByText('Reject'));

    await waitFor(() => {
      expect(rejectCalled).toBe(true);
    });
  });

  test('shows loading state while fetching', () => {
    vi.spyOn(globalThis, 'fetch').mockReturnValue(new Promise(() => {}));
    renderPage();
    expect(screen.getByTestId('loading')).toBeInTheDocument();
  });

  test('invalidates approvals query when a stream event arrives', async () => {
    vi.spyOn(globalThis, 'fetch').mockResolvedValue(
      new Response(JSON.stringify([]), { status: 200, headers: { 'Content-Type': 'application/json' } }),
    );
    renderPageWithSpy();
    await waitFor(() =>
      expect(invalidateSpy).toHaveBeenCalledWith(expect.objectContaining({ queryKey: ['approvals'] })),
    );
  });
});
