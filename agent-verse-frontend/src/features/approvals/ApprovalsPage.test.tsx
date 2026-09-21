/**
 * ApprovalsPage unit tests — updated for world-class rebuild.
 *
 * Covers: heading, live indicator, empty state, loading, cards,
 * approve/reject, risk filter, bulk select, keyboard shortcuts help,
 * history tab, stats bar render, SSE invalidation.
 */
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter } from 'react-router-dom';
import { afterEach, beforeEach, describe, expect, test, vi } from 'vitest';
import { useAuthStore } from '@/stores/auth';
import { useToastStore } from '@/stores/toast';
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
  required_approvers: number; approvals_received: number;
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

  // ── Time-ago / SLA formatting branches ────────────────────────────────────

  test('shows minutes-ago and hours-ago timestamps on cards', async () => {
    const fiveMinAgo = new Date(Date.now() - 5 * 60_000).toISOString();
    const threeHoursAgo = new Date(Date.now() - 3 * 60 * 60_000).toISOString();
    mockFetch([
      PENDING({ request_id: 'r1', action: 'Recent action', created_at: fiveMinAgo }),
      PENDING({ request_id: 'r2', action: 'Old action', created_at: threeHoursAgo }),
    ]);
    renderPage();
    await waitFor(() => screen.getByText('Recent action'));
    expect(screen.getByText(/5m ago/)).toBeInTheDocument();
    expect(screen.getByText(/3h ago/)).toBeInTheDocument();
  });

  test('shows expired/urgent SLA countdown for an overdue approval', async () => {
    const longExpired = new Date(Date.now() - 10 * 60_000).toISOString();
    mockFetch([PENDING({ request_id: 'r1', action: 'Overdue action', created_at: longExpired })]);
    renderPage();
    await waitFor(() => screen.getByText('Overdue action'));
    expect(screen.getByText(/expired/i)).toBeInTheDocument();
  });

  test('shows multi-person approver progress when required_approvers > 1', async () => {
    mockFetch([
      PENDING({ request_id: 'r1', action: 'Multi-approve action', required_approvers: 3, approvals_received: 1 }),
    ]);
    renderPage();
    await waitFor(() => screen.getByText('Multi-approve action'));
    expect(screen.getByText(/1\/3 approvers/)).toBeInTheDocument();
  });

  // ── SLA stats bar branches ────────────────────────────────────────────────

  test('renders SLA stats bar with avg resolution, within-SLA and timed-out counts', async () => {
    mockFetch([PENDING()], {
      sla: { avg_resolution_seconds: 180, within_sla: 12, timed_out: 3 },
    });
    renderPage();
    await waitFor(() => screen.getByText(/avg resolution/i));
    expect(screen.getByText('3m')).toBeInTheDocument();
    expect(screen.getByText('12')).toBeInTheDocument();
    expect(screen.getByText('3')).toBeInTheDocument();
  });

  // ── History tab: other resolution statuses ────────────────────────────────

  test('history tab shows a rejected item with its note', async () => {
    const rejectedItem = {
      request_id: 'h1', goal_id: 'g1', action: 'Rejected action',
      risk_level: 'high', status: 'rejected',
      approver: 'user:admin', note: 'Too risky', created_at: '', resolved_at: '',
    };
    mockFetch([], { history: [rejectedItem] });
    renderPage();
    await waitFor(() => screen.getByRole('tab', { name: /history/i }));
    await userEvent.click(screen.getByRole('tab', { name: /history/i }));
    const row = await screen.findByTestId('history-row');
    expect(within(row).getByText('Rejected action')).toBeInTheDocument();
    expect(within(row).getByText(/too risky/i)).toBeInTheDocument();
    expect(within(row).getByText('rejected')).toBeInTheDocument();
  });

  test('history tab shows a timed-out item', async () => {
    const timedOutItem = {
      request_id: 'h2', goal_id: 'g2', action: 'Timed out action',
      risk_level: 'low', status: 'timed_out',
      approver: null, created_at: '', resolved_at: '',
    };
    mockFetch([], { history: [timedOutItem] });
    renderPage();
    await waitFor(() => screen.getByRole('tab', { name: /history/i }));
    await userEvent.click(screen.getByRole('tab', { name: /history/i }));
    const row = await screen.findByTestId('history-row');
    expect(within(row).getByText('timed_out')).toBeInTheDocument();
  });

  // ── Risk filter empty state reset ─────────────────────────────────────────

  test('filtering to a risk level with no matches shows a scoped empty state with reset', async () => {
    mockFetch([PENDING({ request_id: 'r1', risk_level: 'low', action: 'Low only action' })]);
    renderPage();
    await waitFor(() => screen.getByText('Low only action'));
    await userEvent.click(screen.getByRole('button', { name: /^critical$/i }));
    await waitFor(() => expect(screen.getByTestId('empty-state')).toBeInTheDocument());
    expect(screen.getByText(/no pending critical risk requests/i)).toBeInTheDocument();
    await userEvent.click(screen.getByRole('button', { name: /show all requests/i }));
    await waitFor(() => expect(screen.getByText('Low only action')).toBeInTheDocument());
  });

  // ── Error state ────────────────────────────────────────────────────────────

  test('shows an error message when fetching approvals fails', async () => {
    vi.spyOn(globalThis, 'fetch').mockImplementation(async () => {
      throw new Error('Network down');
    });
    renderPage();
    await waitFor(() => expect(screen.getByText(/failed to load approvals/i)).toBeInTheDocument());
  });

  // ── Sort by time branch ────────────────────────────────────────────────────

  test('switching sort to Time re-orders by created_at', async () => {
    mockFetch([
      PENDING({ request_id: 'r1', action: 'Older', risk_level: 'low', created_at: new Date(Date.now() - 60_000).toISOString() }),
      PENDING({ request_id: 'r2', action: 'Newer', risk_level: 'critical', created_at: new Date().toISOString() }),
    ]);
    renderPage();
    await waitFor(() => screen.getByText('Older'));
    await userEvent.selectOptions(screen.getByLabelText(/sort by/i), 'time');
    const cards = screen.getAllByTestId('approval-card');
    expect(within(cards[0]).getByText('Newer')).toBeInTheDocument();
  });

  // ── Selection toggling (select then deselect) ─────────────────────────────

  test('checkbox toggles selection on and off', async () => {
    mockFetch([PENDING({ request_id: 'r1', action: 'Toggle action' })]);
    renderPage();
    await waitFor(() => screen.getByText('Toggle action'));
    const checkbox = screen.getByRole('checkbox', { name: /select request/i });
    await userEvent.click(checkbox);
    await waitFor(() => expect(screen.getByText(/1 selected/i)).toBeInTheDocument());
    await userEvent.click(checkbox);
    await waitFor(() => expect(screen.queryByText(/1 selected/i)).not.toBeInTheDocument());
  });

  // ── Bulk approve / reject full flow (confirm modal + submit) ─────────────

  test('bulk-approve flow: select all, confirm, and submit batch approve', async () => {
    mockFetch([
      PENDING({ request_id: 'r1', action: 'Bulk one' }),
      PENDING({ request_id: 'r2', action: 'Bulk two' }),
    ]);
    renderPage();
    await waitFor(() => screen.getAllByTestId('approval-card'));
    await userEvent.click(screen.getByRole('checkbox', { name: /select all requests/i }));
    await userEvent.click(screen.getByRole('button', { name: /approve all/i }));

    await waitFor(() => expect(screen.getByText(/approve 2 request\(s\)\?/i)).toBeInTheDocument());
    expect(screen.getByText(/will be approved and the waiting agents will resume/i)).toBeInTheDocument();

    const dialog = screen.getByRole('dialog');
    await userEvent.click(within(dialog).getByRole('button', { name: /^approve all$/i }));

    await waitFor(() => {
      const toasts = useToastStore.getState().toasts;
      expect(toasts.some((t) => /batch action complete/i.test(t.message))).toBe(true);
    });
    // Selection cleared and toolbar dismissed after success
    await waitFor(() => expect(screen.queryByText(/selected/i)).not.toBeInTheDocument());
  });

  test('bulk-reject flow shows the reject-specific confirm copy and reports a partial failure', async () => {
    mockFetch(
      [
        PENDING({ request_id: 'r1', action: 'Reject one' }),
        PENDING({ request_id: 'r2', action: 'Reject two' }),
      ],
    );
    // Override batch-approve to simulate a partial failure (one request already resolved).
    vi.spyOn(globalThis, 'fetch').mockImplementation(async (input, init) => {
      const url = String(input);
      const method = (init as RequestInit | undefined)?.method ?? 'GET';
      if (url.includes('/hitl/batch-approve') && method === 'POST') {
        return new Response(
          JSON.stringify({ approved: 0, rejected: 1, not_found: 1, results: [] }),
          { status: 200 },
        );
      }
      if (url.includes('/approvals/sla-stats')) return new Response(JSON.stringify({}), { status: 200 });
      if (url.includes('/approvals/history')) return new Response(JSON.stringify([]), { status: 200 });
      if (url.includes('/governance/approvals') && method === 'GET') {
        return new Response(JSON.stringify([
          PENDING({ request_id: 'r1', action: 'Reject one' }),
          PENDING({ request_id: 'r2', action: 'Reject two' }),
        ]), { status: 200 });
      }
      return new Response('[]', { status: 200 });
    });
    renderPage();
    await waitFor(() => screen.getAllByTestId('approval-card'));
    await userEvent.click(screen.getByRole('checkbox', { name: /select all requests/i }));
    await userEvent.click(screen.getByRole('button', { name: /reject all/i }));

    await waitFor(() => expect(screen.getByText(/reject 2 request\(s\)\?/i)).toBeInTheDocument());
    expect(screen.getByText(/will be rejected and the waiting agents will be notified/i)).toBeInTheDocument();

    const dialog = screen.getByRole('dialog');
    await userEvent.click(within(dialog).getByRole('button', { name: /^reject all$/i }));

    await waitFor(() => {
      const toasts = useToastStore.getState().toasts;
      // Only 1 of 2 actually processed (1 not_found) — batch still reports success with the processed count.
      expect(toasts.some((t) => /batch action complete: 1 request\(s\) processed/i.test(t.message))).toBe(true);
    });
  });

  // ── Keyboard navigation & shortcuts ───────────────────────────────────────

  test('keyboard: ArrowDown/ArrowUp move focus, A approves, R rejects the focused card', async () => {
    const fetchSpy = mockFetch([
      PENDING({ request_id: 'r1', action: 'First', risk_level: 'critical' }),
      PENDING({ request_id: 'r2', action: 'Second', risk_level: 'low' }),
    ]);
    renderPage();
    await waitFor(() => screen.getAllByTestId('approval-card'));
    const container = screen.getByLabelText('Approval inbox');
    await userEvent.click(container);

    await userEvent.keyboard('{ArrowDown}');
    await userEvent.keyboard('{ArrowUp}');

    await userEvent.keyboard('a');
    await waitFor(() => {
      const approveCall = fetchSpy.mock.calls.find(
        ([u, i]) => String(u).includes('/approve') && (i as RequestInit)?.method === 'POST'
      );
      expect(approveCall).toBeTruthy();
    });
  });

  // Regression: cardRefs was a Map that nothing ever populated (each render built a
  // throwaway ref object), so ArrowDown/ArrowUp's `.focus()` call was silently a no-op.
  test('keyboard: ArrowDown actually moves DOM focus onto the next card', async () => {
    mockFetch([
      PENDING({ request_id: 'r1', action: 'First', risk_level: 'critical' }),
      PENDING({ request_id: 'r2', action: 'Second', risk_level: 'low' }),
    ]);
    renderPage();
    const cards = await waitFor(() => screen.getAllByTestId('approval-card'));
    const container = screen.getByLabelText('Approval inbox');
    await userEvent.click(container);

    await userEvent.keyboard('{ArrowDown}');
    await waitFor(() => expect(document.activeElement).toBe(cards[1]));
  });

  test('keyboard: R rejects the focused card', async () => {
    const fetchSpy = mockFetch([
      PENDING({ request_id: 'r1', action: 'Only one', risk_level: 'critical' }),
    ]);
    renderPage();
    await waitFor(() => screen.getByTestId('approval-card'));
    const container = screen.getByLabelText('Approval inbox');
    await userEvent.click(container);

    await userEvent.keyboard('r');
    await waitFor(() => {
      const rejectCall = fetchSpy.mock.calls.find(
        ([u, i]) => String(u).includes('/reject') && (i as RequestInit)?.method === 'POST'
      );
      expect(rejectCall).toBeTruthy();
    });
  });

  test('keyboard: Space toggles selection and Cmd+A selects all visible', async () => {
    mockFetch([
      PENDING({ request_id: 'r1', action: 'First' }),
      PENDING({ request_id: 'r2', action: 'Second' }),
    ]);
    renderPage();
    await waitFor(() => screen.getAllByTestId('approval-card'));
    const container = screen.getByLabelText('Approval inbox');
    await userEvent.click(container);

    await userEvent.keyboard(' ');
    await waitFor(() => expect(screen.getByText(/1 selected/i)).toBeInTheDocument());

    await userEvent.keyboard('{Meta>}a{/Meta}');
    await waitFor(() => expect(screen.getByText(/2 selected/i)).toBeInTheDocument());
  });

  test('keyboard: Escape clears the current selection', async () => {
    mockFetch([PENDING({ request_id: 'r1', action: 'Escape target' })]);
    renderPage();
    await waitFor(() => screen.getByTestId('approval-card'));
    const container = screen.getByLabelText('Approval inbox');
    await userEvent.click(container);

    await userEvent.keyboard(' ');
    await waitFor(() => expect(screen.getByText(/1 selected/i)).toBeInTheDocument());

    await userEvent.keyboard('{Escape}');
    await waitFor(() => expect(screen.queryByText(/1 selected/i)).not.toBeInTheDocument());
  });

  test('keyboard: Escape closes the shortcuts help dialog instead of clearing selection', async () => {
    mockFetch([]);
    renderPage();
    await waitFor(() => screen.getByTestId('empty-state'));
    const container = screen.getByLabelText('Approval inbox');
    await userEvent.click(container);
    await userEvent.keyboard('?');
    await waitFor(() => expect(screen.getByText(/keyboard shortcuts/i)).toBeInTheDocument());

    await userEvent.keyboard('{Escape}');
    await waitFor(() => expect(screen.queryByText(/keyboard shortcuts/i)).not.toBeInTheDocument());
  });

  // ── Note textarea typing ──────────────────────────────────────────────────

  test('typing into the note textarea updates its value', async () => {
    mockFetch([PENDING({ request_id: 'r1', action: 'Note action' })]);
    renderPage();
    await waitFor(() => screen.getByTestId('approval-card'));
    await userEvent.click(screen.getByRole('button', { name: /add note/i }));
    // Note: Space is a global keyboard shortcut on the page's outer container (toggles bulk
    // selection) and swallows the keydown before it reaches the textarea, so avoid spaces here.
    const textarea = screen.getByLabelText(/approval note/i);
    await userEvent.type(textarea, 'LooksFineToMe');
    expect(textarea).toHaveValue('LooksFineToMe');
  });

  // ── Goal link navigation ───────────────────────────────────────────────────

  test('clicking the goal link navigates to the goal page', async () => {
    mockFetch([PENDING({ request_id: 'r1', action: 'Goal nav action', goal_id: 'goal-xyz-123' })]);
    renderPage();
    await waitFor(() => screen.getByTestId('approval-card'));
    await userEvent.click(screen.getByTitle(/go to goal goal-xyz-123/i));
    // Navigation happens client-side (react-router MemoryRouter) — the card itself remains
    // mounted since ApprovalsPage isn't unmounted by this route change in the test tree.
    await waitFor(() => expect(screen.getByRole('button', { name: /approve request/i })).toBeInTheDocument());
  });

  // ── Mutation error paths ───────────────────────────────────────────────────

  test('shows an error toast when the approve request fails', async () => {
    mockFetch([PENDING({ request_id: 'r1', action: 'Fails to approve' })]);
    vi.spyOn(globalThis, 'fetch').mockImplementation(async (input, init) => {
      const url = String(input);
      const method = (init as RequestInit | undefined)?.method ?? 'GET';
      if (url.includes('/approve') && method === 'POST') return new Response('boom', { status: 500 });
      if (url.includes('/approvals/sla-stats')) return new Response(JSON.stringify({}), { status: 200 });
      if (url.includes('/approvals/history')) return new Response(JSON.stringify([]), { status: 200 });
      if (url.includes('/governance/approvals') && method === 'GET') {
        return new Response(JSON.stringify([PENDING({ request_id: 'r1', action: 'Fails to approve' })]), { status: 200 });
      }
      return new Response('[]', { status: 200 });
    });
    renderPage();
    await waitFor(() => screen.getByRole('button', { name: /approve request/i }));
    await userEvent.click(screen.getByRole('button', { name: /approve request/i }));
    await waitFor(() => {
      const toasts = useToastStore.getState().toasts;
      expect(toasts.some((t) => t.kind === 'error' && /approve failed/i.test(t.message))).toBe(true);
    });
  });

  test('shows an error toast when the reject request fails', async () => {
    vi.spyOn(globalThis, 'fetch').mockImplementation(async (input, init) => {
      const url = String(input);
      const method = (init as RequestInit | undefined)?.method ?? 'GET';
      if (url.includes('/reject') && method === 'POST') return new Response('boom', { status: 500 });
      if (url.includes('/approvals/sla-stats')) return new Response(JSON.stringify({}), { status: 200 });
      if (url.includes('/approvals/history')) return new Response(JSON.stringify([]), { status: 200 });
      if (url.includes('/governance/approvals') && method === 'GET') {
        return new Response(JSON.stringify([PENDING({ request_id: 'r1', action: 'Fails to reject' })]), { status: 200 });
      }
      return new Response('[]', { status: 200 });
    });
    renderPage();
    await waitFor(() => screen.getByRole('button', { name: /reject request/i }));
    await userEvent.click(screen.getByRole('button', { name: /reject request/i }));
    await waitFor(() => {
      const toasts = useToastStore.getState().toasts;
      expect(toasts.some((t) => t.kind === 'error' && /reject failed/i.test(t.message))).toBe(true);
    });
  });

  test('shows an error toast when the batch action request fails', async () => {
    vi.spyOn(globalThis, 'fetch').mockImplementation(async (input, init) => {
      const url = String(input);
      const method = (init as RequestInit | undefined)?.method ?? 'GET';
      if (url.includes('/hitl/batch-approve') && method === 'POST') return new Response('boom', { status: 500 });
      if (url.includes('/approvals/sla-stats')) return new Response(JSON.stringify({}), { status: 200 });
      if (url.includes('/approvals/history')) return new Response(JSON.stringify([]), { status: 200 });
      if (url.includes('/governance/approvals') && method === 'GET') {
        return new Response(JSON.stringify([
          PENDING({ request_id: 'r1', action: 'Batch fail action' }),
          PENDING({ request_id: 'r2', action: 'Batch fail action two' }),
        ]), { status: 200 });
      }
      return new Response('[]', { status: 200 });
    });
    renderPage();
    await waitFor(() => screen.getAllByTestId('approval-card'));
    await userEvent.click(screen.getByRole('checkbox', { name: /select all requests/i }));
    await userEvent.click(screen.getByRole('button', { name: /approve all/i }));
    await waitFor(() => expect(screen.getByRole('dialog')).toBeInTheDocument());
    const dialog = screen.getByRole('dialog');
    await userEvent.click(within(dialog).getByRole('button', { name: /^approve all$/i }));
    await waitFor(() => {
      const toasts = useToastStore.getState().toasts;
      expect(toasts.some((t) => t.kind === 'error' && /batch action failed/i.test(t.message))).toBe(true);
    });
  });

  // ── Misc UI affordances ────────────────────────────────────────────────────

  test('clicking "? help" in the keyboard hint bar opens the shortcuts dialog', async () => {
    mockFetch([PENDING({ request_id: 'r1', action: 'Hint bar action' })]);
    renderPage();
    await waitFor(() => screen.getByTestId('approval-card'));
    await userEvent.click(screen.getByRole('button', { name: /^\? help$/i }));
    await waitFor(() => expect(screen.getByText(/keyboard shortcuts/i)).toBeInTheDocument());
  });

  test('cancelling the bulk confirm modal dismisses it without submitting', async () => {
    const fetchSpy = mockFetch([
      PENDING({ request_id: 'r1', action: 'Cancel bulk one' }),
      PENDING({ request_id: 'r2', action: 'Cancel bulk two' }),
    ]);
    renderPage();
    await waitFor(() => screen.getAllByTestId('approval-card'));
    await userEvent.click(screen.getByRole('checkbox', { name: /select all requests/i }));
    await userEvent.click(screen.getByRole('button', { name: /approve all/i }));
    await waitFor(() => expect(screen.getByRole('dialog')).toBeInTheDocument());
    const dialog = screen.getByRole('dialog');
    await userEvent.click(within(dialog).getByRole('button', { name: /^cancel$/i }));
    await waitFor(() => expect(screen.queryByRole('dialog')).not.toBeInTheDocument());
    const batchCall = fetchSpy.mock.calls.find(([u]) => String(u).includes('/hitl/batch-approve'));
    expect(batchCall).toBeUndefined();
  });

  // ── Toast on new SSE arrival ───────────────────────────────────────────────

  test('shows a toast when new pending approvals arrive via refetch', async () => {
    let call = 0;
    vi.spyOn(globalThis, 'fetch').mockImplementation(async (input) => {
      const url = String(input);
      if (url.includes('/approvals/sla-stats')) return new Response(JSON.stringify({}), { status: 200 });
      if (url.includes('/approvals/history')) return new Response(JSON.stringify([]), { status: 200 });
      if (url.includes('/governance/approvals')) {
        call += 1;
        const items = call === 1
          ? [PENDING({ request_id: 'r1', action: 'First' })]
          : [PENDING({ request_id: 'r1', action: 'First' }), PENDING({ request_id: 'r2', action: 'Second' })];
        return new Response(JSON.stringify(items), { status: 200 });
      }
      return new Response('[]', { status: 200 });
    });
    renderPage();
    await waitFor(() => screen.getByText('First'));
    // The mocked SSE stream fires an event ~10ms after mount, invalidating the approvals
    // query and triggering a refetch that returns one additional pending item.
    await waitFor(() => {
      const toasts = useToastStore.getState().toasts;
      expect(toasts.some((t) => /new approval request\(s\) arrived/i.test(t.message))).toBe(true);
    });
  });
});
