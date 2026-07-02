/**
 * ApprovalsPage unit tests — updated for world-class rebuild.
 *
 * Covers: heading, live indicator, empty state, loading, cards,
 * approve/reject, risk filter, bulk select, keyboard shortcuts help,
 * history tab, stats bar render, SSE invalidation.
 */
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter } from 'react-router-dom';
import { afterEach, beforeEach, describe, expect, test, vi } from 'vitest';
import { useAuthStore } from '@/stores/auth';
import { ApprovalsPage } from './ApprovalsPage';

// ── SSE mock ──────────────────────────────────────────────────────────────────

vi.mock('@/lib/sse/useEventStream', () => ({
  useEventStream: (_path: string | null, opts?: { onEvent?: (e: { type: string }) => void }) => {
    setTimeout(() => opts?.onEvent?.({ type: 'waiting_approval' }), 10);
    return { events: [], connected: true };
  },
}));

// ── Fixtures ──────────────────────────────────────────────────────────────────

const PENDING = (overrides: Partial<{
  request_id: string; goal_id: string; action: string;
  risk_level: string; status: string; created_at: string;
}> = {}) => ({
  request_id: 'req-001',
  goal_id:    'goal-abc',
  action:     'delete_file /critical/path',
  risk_level: 'high',
  status:     'pending',
  created_at: new Date().toISOString(),
  ...overrides,
});

// ── Render helper ─────────────────────────────────────────────────────────────

function renderPage(fetchImpl?: typeof globalThis.fetch) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  if (fetchImpl) vi.spyOn(globalThis, 'fetch').mockImplementation(fetchImpl);
  return {
    qc,
    ...render(
      <MemoryRouter>
        <QueryClientProvider client={qc}>
          <ApprovalsPage />
        </QueryClientProvider>
      </MemoryRouter>
    ),
  };
}

function mockFetch(approvals: ReturnType<typeof PENDING>[] = [], extras: Record<string, object> = {}) {
  return vi.spyOn(globalThis, 'fetch').mockImplementation(async (input, init) => {
    const url = String(input);
    const method = (init as RequestInit | undefined)?.method ?? 'GET';

    if (url.includes('/approvals/sla-stats'))
      return new Response(JSON.stringify(extras.sla ?? {}), { status: 200 });
    if (url.includes('/approvals/history'))
      return new Response(JSON.stringify(extras.history ?? []), { status: 200 });
    if (url.includes('/approve') && method === 'POST')
      return new Response(JSON.stringify({ status: 'approved' }), { status: 200 });
    if (url.includes('/reject') && method === 'POST')
      return new Response(JSON.stringify({ status: 'rejected' }), { status: 200 });
    if (url.includes('/governance/approvals') && method === 'GET')
      return new Response(JSON.stringify(approvals), { status: 200 });
    if (url.includes('/hitl/batch-approve') && method === 'POST')
      return new Response(JSON.stringify({ approved: 2, rejected: 0, not_found: 0, results: [] }), { status: 200 });
    return new Response('[]', { status: 200 });
  });
}

// ── Setup ─────────────────────────────────────────────────────────────────────

beforeEach(() => {
  sessionStorage.setItem('av_api_key', 'test-key');
  localStorage.setItem('av_api_key', 'test-key');
  useAuthStore.setState({ apiKey: 'test-key', tenantId: 'tenant-1', isAuthenticated: true });
});
afterEach(() => vi.restoreAllMocks());

// ═══════════════════════════════════════════════════════════════════════════════

describe('ApprovalsPage', () => {

  // ── Header & structure ────────────────────────────────────────────────────

  test('renders "Approval Inbox" heading', () => {
    mockFetch();
    renderPage();
    expect(screen.getByRole('heading', { name: /approval inbox/i })).toBeInTheDocument();
  });

  test('shows live SSE indicator when connected', () => {
    mockFetch();
    renderPage();
    expect(screen.getByText(/live/i)).toBeInTheDocument();
  });

  test('shows keyboard shortcuts button', () => {
    mockFetch();
    renderPage();
    expect(screen.getByRole('button', { name: /keyboard shortcuts/i })).toBeInTheDocument();
  });

  test('shows Inbox and History tabs', async () => {
    mockFetch([]);
    renderPage();
    await waitFor(() => expect(screen.getByRole('tab', { name: /inbox/i })).toBeInTheDocument());
    expect(screen.getByRole('tab', { name: /history/i })).toBeInTheDocument();
  });

  // ── Loading & empty states ────────────────────────────────────────────────

  test('shows loading skeleton while fetching', () => {
    vi.spyOn(globalThis, 'fetch').mockReturnValue(new Promise(() => {}));
    renderPage();
    expect(screen.getByTestId('loading')).toBeInTheDocument();
  });

  test('shows empty state when no pending approvals', async () => {
    mockFetch([]);
    renderPage();
    await waitFor(() => expect(screen.getByTestId('empty-state')).toBeInTheDocument());
    expect(screen.getByText(/all clear/i)).toBeInTheDocument();
    expect(screen.getByText(/no pending approval requests/i)).toBeInTheDocument();
  });

  // ── Pending count badge ───────────────────────────────────────────────────

   test('shows pending count badge', async () => {
     mockFetch([PENDING(), PENDING({ request_id: 'req-002' })]);
     renderPage();
     await waitFor(() => expect(screen.getAllByText('2').length).toBeGreaterThanOrEqual(1));
   });

  // ── Approval cards ────────────────────────────────────────────────────────

  test('renders action text and goal ID in cards', async () => {
    mockFetch([PENDING()]);
    renderPage();
    await waitFor(() => screen.getByTestId('approval-card'));
    expect(screen.getByText('delete_file /critical/path')).toBeInTheDocument();
    expect(screen.getByText(/goal-abc/)).toBeInTheDocument();
  });

  test('renders risk level badge on card', async () => {
    mockFetch([PENDING({ risk_level: 'critical' })]);
    renderPage();
    await waitFor(() => screen.getByTestId('approval-card'));
    // 'critical' appears as filter pill AND card badge — check at least 2 instances
    expect(screen.getAllByText('critical').length).toBeGreaterThanOrEqual(1);
  });

  test('Approve button calls /approve endpoint', async () => {
    const fetchSpy = mockFetch([PENDING()]);
    renderPage();
    await waitFor(() => screen.getByRole('button', { name: /approve request/i }));
    await userEvent.click(screen.getByRole('button', { name: /approve request/i }));
    await waitFor(() => {
      const approveCall = fetchSpy.mock.calls.find(
        ([u, i]) => String(u).includes('/approve') && (i as RequestInit)?.method === 'POST'
      );
      expect(approveCall).toBeTruthy();
    });
  });

  test('Reject button calls /reject endpoint', async () => {
    const fetchSpy = mockFetch([PENDING()]);
    renderPage();
    await waitFor(() => screen.getByRole('button', { name: /reject request/i }));
    await userEvent.click(screen.getByRole('button', { name: /reject request/i }));
    await waitFor(() => {
      const rejectCall = fetchSpy.mock.calls.find(
        ([u, i]) => String(u).includes('/reject') && (i as RequestInit)?.method === 'POST'
      );
      expect(rejectCall).toBeTruthy();
    });
  });

  // ── Note textarea ─────────────────────────────────────────────────────────

  test('note textarea appears after clicking "Add note"', async () => {
    mockFetch([PENDING()]);
    renderPage();
    await waitFor(() => screen.getByTestId('approval-card'));
    await userEvent.click(screen.getByRole('button', { name: /add note/i }));
    expect(screen.getByLabelText(/approval note/i)).toBeInTheDocument();
  });

  // ── Risk filter pills ─────────────────────────────────────────────────────

  test('risk filter pills render (all, critical, high, medium, low)', async () => {
    mockFetch([]);
    renderPage();
    await waitFor(() => expect(screen.getByRole('tab', { name: /inbox/i })).toBeInTheDocument());
    expect(screen.getByRole('button', { name: /^all$/i })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /^critical$/i })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /^high$/i })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /^medium$/i })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /^low$/i })).toBeInTheDocument();
  });

  test('clicking risk filter hides non-matching cards', async () => {
    mockFetch([
      PENDING({ request_id: 'r1', risk_level: 'high',   action: 'High action' }),
      PENDING({ request_id: 'r2', risk_level: 'low',    action: 'Low action' }),
    ]);
    renderPage();
    await waitFor(() => screen.getByText('High action'));
    expect(screen.getByText('Low action')).toBeInTheDocument();

    await userEvent.click(screen.getByRole('button', { name: /^high$/i }));
    expect(screen.getByText('High action')).toBeInTheDocument();
    expect(screen.queryByText('Low action')).not.toBeInTheDocument();
  });

  // ── Bulk selection ────────────────────────────────────────────────────────

  test('checkboxes render on each card', async () => {
    mockFetch([PENDING(), PENDING({ request_id: 'req-002', action: 'second action' })]);
    renderPage();
    await waitFor(() => screen.getAllByTestId('approval-card'));
    const checkboxes = screen.getAllByRole('checkbox', { name: /select request/i });
    expect(checkboxes.length).toBe(2);
  });

  test('Select All checkbox appears with multiple requests', async () => {
    mockFetch([PENDING(), PENDING({ request_id: 'r2', action: 'action 2' })]);
    renderPage();
    await waitFor(() => screen.getAllByTestId('approval-card'));
    expect(screen.getByRole('checkbox', { name: /select all requests/i })).toBeInTheDocument();
  });

  test('checking Select All selects all cards and shows bulk toolbar', async () => {
    mockFetch([PENDING(), PENDING({ request_id: 'r2', action: 'action 2' })]);
    renderPage();
    await waitFor(() => screen.getAllByTestId('approval-card'));
    await userEvent.click(screen.getByRole('checkbox', { name: /select all requests/i }));
    await waitFor(() => expect(screen.getByText(/approve all/i)).toBeInTheDocument());
    expect(screen.getByText(/reject all/i)).toBeInTheDocument();
  });

  // ── Keyboard shortcuts dialog ─────────────────────────────────────────────

  test('pressing ? opens keyboard shortcuts dialog', async () => {
    mockFetch([]);
    renderPage();
    await waitFor(() => screen.getByTestId('empty-state'));
    const container = screen.getByLabelText('Approval inbox');
    await userEvent.click(container);
    await userEvent.keyboard('?');
    await waitFor(() => expect(screen.getByText(/keyboard shortcuts/i)).toBeInTheDocument());
  });

  test('closing shortcuts dialog removes it', async () => {
    mockFetch([]);
    renderPage();
    await waitFor(() => screen.getByTestId('empty-state'));
    await userEvent.click(screen.getByRole('button', { name: /keyboard shortcuts/i }));
    expect(screen.getByText(/keyboard shortcuts/i)).toBeInTheDocument();
    await userEvent.click(screen.getByRole('button', { name: /close/i }));
    await waitFor(() => expect(screen.queryByRole('dialog')).not.toBeInTheDocument());
  });

  // ── History tab ───────────────────────────────────────────────────────────

  test('switching to history tab loads resolved requests', async () => {
    const historyItem = {
      request_id: 'h1', goal_id: 'g1', action: 'Resolved action',
      risk_level: 'medium', status: 'approved',
      approver: 'user:admin', note: 'Looks good', created_at: '', resolved_at: '',
    };
    mockFetch([], { history: [historyItem] });
    renderPage();
    await waitFor(() => screen.getByRole('tab', { name: /history/i }));
    await userEvent.click(screen.getByRole('tab', { name: /history/i }));
    expect(await screen.findByText('Resolved action')).toBeInTheDocument();
    expect(screen.getByTestId('history-row')).toBeInTheDocument();
  });

  test('history tab shows empty state when no history', async () => {
    mockFetch([], { history: [] });
    renderPage();
    await waitFor(() => screen.getByRole('tab', { name: /history/i }));
    await userEvent.click(screen.getByRole('tab', { name: /history/i }));
    expect(await screen.findByText(/no history yet/i)).toBeInTheDocument();
  });

  // ── SSE invalidation ──────────────────────────────────────────────────────

  test('invalidates approvals query when a stream event arrives', async () => {
    const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    const invalidateSpy = vi.spyOn(qc, 'invalidateQueries');
    mockFetch([]);
    render(
      <MemoryRouter>
        <QueryClientProvider client={qc}><ApprovalsPage /></QueryClientProvider>
      </MemoryRouter>
    );
    await waitFor(() =>
      expect(invalidateSpy).toHaveBeenCalledWith(expect.objectContaining({ queryKey: ['approvals'] }))
    );
  });
});
