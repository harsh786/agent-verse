import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter } from 'react-router-dom';
import { afterEach, beforeEach, describe, expect, test, vi } from 'vitest';
import { useAuthStore } from '@/stores/auth';
import { ApprovalsPage } from './ApprovalsPage';

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

  // Test 1: Renders "Approval Inbox" heading
  test('renders "Approval Inbox" heading', () => {
    vi.spyOn(globalThis, 'fetch').mockReturnValue(new Promise(() => {}));
    renderPage();
    expect(screen.getByText('Approval Inbox')).toBeInTheDocument();
  });

  // Test 2: Shows pending count badge when approvals exist
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

  // Test 3: Shows empty state when no pending approvals
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

  // Test 4: Approve button calls approve API
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

  // Test 5: Reject button calls reject API
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

  // Test 6: Shows loading state
  test('shows loading state while fetching', () => {
    vi.spyOn(globalThis, 'fetch').mockReturnValue(new Promise(() => {}));
    renderPage();
    expect(screen.getByTestId('loading')).toBeInTheDocument();
  });
});
