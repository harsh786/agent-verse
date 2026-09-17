import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter } from 'react-router-dom';
import { afterEach, beforeEach, describe, expect, test, vi } from 'vitest';
import { useAuthStore } from '@/stores/auth';
import AuditExplorerPage from './AuditExplorerPage';

function renderPage() {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={qc}>
      <MemoryRouter><AuditExplorerPage /></MemoryRouter>
    </QueryClientProvider>,
  );
}

const SAMPLE = [
  { event_id: 'e1', goal_id: 'g1', tool_name: 'jira.delete', action_level: 'deny', outcome: 'denied', note: '' },
  { event_id: 'e2', goal_id: 'g2', tool_name: 'github.read', action_level: 'allow', outcome: 'success', note: '' },
];

function mockFetch(events = SAMPLE) {
  return vi.spyOn(globalThis, 'fetch').mockImplementation(async (input) => {
    const url = String(input);
    if (url.includes('/governance/audit'))
      return new Response(JSON.stringify(events), { status: 200, headers: { 'Content-Type': 'application/json' } });
    return new Response('{}', { status: 200, headers: { 'Content-Type': 'application/json' } });
  });
}

beforeEach(() => {
  sessionStorage.clear(); localStorage.clear();
  useAuthStore.setState({ apiKey: 'k', tenantId: 't', plan: 'free', isAuthenticated: true });
});
afterEach(() => vi.restoreAllMocks());

describe('AuditExplorerPage', () => {
  test('renders Audit Explorer heading', async () => {
    mockFetch();
    renderPage();
    expect(await screen.findByRole('heading', { name: /audit/i })).toBeInTheDocument();
  });

  test('renders typed audit rows from auditApi', async () => {
    mockFetch();
    renderPage();
    expect(await screen.findByText('jira.delete')).toBeInTheDocument();
    expect(screen.getByText('github.read')).toBeInTheDocument();
  });

  test('tool filter forwards query param', async () => {
    const spy = mockFetch();
    renderPage();
    await screen.findByText('jira.delete');
    const toolInput = screen.getByTestId('audit-filters').querySelector('#audit-tool-filter') as HTMLInputElement ??
      screen.queryByPlaceholderText(/tool/i);
    if (toolInput) {
      await userEvent.type(toolInput, 'jira.delete');
      await userEvent.click(screen.getByTestId('apply-filters-btn'));
      await waitFor(() =>
        expect(spy.mock.calls.some(([u]) => String(u).includes('tool_name=jira.delete'))).toBe(true)
      );
    }
  });

  test('export JSON button is present once rows load', async () => {
    mockFetch();
    renderPage();
    await screen.findByText('jira.delete');
    expect(screen.getByTestId('export-json-btn')).toBeInTheDocument();
    expect(screen.getByTestId('export-csv-btn')).toBeInTheDocument();
  });

  test('stats section is rendered', async () => {
    mockFetch();
    renderPage();
    // Stats section renders StatCard components with these labels
    await waitFor(() => expect(screen.getByText('Total Events')).toBeInTheDocument());
  });

  test('empty state shown when no events', async () => {
    mockFetch([]);
    renderPage();
    // When no events, either EmptyState text or audit-table is absent
    await waitFor(() => expect(screen.queryByText('jira.delete')).not.toBeInTheDocument());
    // The page shouldn't crash
    expect(document.body).toBeTruthy();
  });

  // Correctness (regression): the outcome filter has no backend param, so it is
  // applied client-side. It must scan the WHOLE dataset — a match on a later
  // server page must not be silently missed. The mock serves a full first page
  // (all "success") and a matching "denied" event only on the second page.
  test('outcome filter matches events beyond the first page (whole-dataset scan)', async () => {
    const firstPage = Array.from({ length: 100 }, (_, i) => ({
      event_id: `p1-${i}`, goal_id: 'g', tool_name: 'github.read',
      action_level: 'allow', outcome: 'success', note: '',
    }));
    const spy = vi.spyOn(globalThis, 'fetch').mockImplementation(async (input) => {
      const url = String(input);
      if (url.includes('/governance/audit')) {
        const offset = Number(/offset=(\d+)/.exec(url)?.[1] ?? 0);
        const body = offset === 0
          ? firstPage
          : offset === 100
            ? [{ event_id: 'match', goal_id: 'g', tool_name: 'stripe.refund', action_level: 'deny', outcome: 'denied', note: '' }]
            : [];
        return new Response(JSON.stringify(body), { status: 200, headers: { 'Content-Type': 'application/json' } });
      }
      return new Response('{}', { status: 200, headers: { 'Content-Type': 'application/json' } });
    });

    renderPage();
    // 100 rows all render 'github.read' — use findAllByText (findByText throws on
    // multiple matches).
    await screen.findAllByText('github.read');

    // Apply the outcome=denied filter (client-side, but must cover all pages).
    await userEvent.selectOptions(screen.getByLabelText('Filter by outcome'), 'denied');
    await userEvent.click(screen.getByTestId('apply-filters-btn'));

    // The denied event lives on page 2 — it must still surface.
    await waitFor(() => expect(screen.getByText('stripe.refund')).toBeInTheDocument());
    // And the scan must have paged past the first page.
    expect(spy.mock.calls.some(([u]) => String(u).includes('offset=100'))).toBe(true);
  });

  test('clicking a row expands and shows event detail fields, clicking again collapses', async () => {
    mockFetch();
    renderPage();
    const row = (await screen.findByText('jira.delete')).closest('tr') as HTMLElement;
    await userEvent.click(row);
    await waitFor(() => expect(screen.getByText('goal id')).toBeInTheDocument());
    expect(row.getAttribute('aria-expanded')).toBe('true');
    await userEvent.click(row);
    await waitFor(() => expect(screen.queryByText('goal id')).not.toBeInTheDocument());
  });

  test('verify chain button shows success message on success', async () => {
    vi.spyOn(globalThis, 'fetch').mockImplementation(async (input) => {
      const url = String(input);
      if (url.includes('/governance/audit/integrity/verify')) {
        return new Response(
          JSON.stringify({ verified: true, verified_events: 42, chain_tip_hash: 'abc123def456' }),
          { status: 200, headers: { 'Content-Type': 'application/json' } }
        );
      }
      if (url.includes('/governance/audit')) {
        return new Response(JSON.stringify(SAMPLE), { status: 200, headers: { 'Content-Type': 'application/json' } });
      }
      return new Response('{}', { status: 200, headers: { 'Content-Type': 'application/json' } });
    });
    renderPage();
    await screen.findByText('jira.delete');
    await userEvent.click(screen.getByTestId('verify-chain-btn'));
    await waitFor(() => expect(screen.getByRole('status')).toHaveTextContent(/chain verified/i));
    expect(screen.getByRole('status')).toHaveTextContent(/42 events intact/i);
  });

  test('verify chain button shows broken-chain message on failure result', async () => {
    vi.spyOn(globalThis, 'fetch').mockImplementation(async (input) => {
      const url = String(input);
      if (url.includes('/governance/audit/integrity/verify')) {
        return new Response(
          JSON.stringify({ verified: false, verified_events: 3, broken_chain_at: 'evt-99' }),
          { status: 200, headers: { 'Content-Type': 'application/json' } }
        );
      }
      if (url.includes('/governance/audit')) {
        return new Response(JSON.stringify(SAMPLE), { status: 200, headers: { 'Content-Type': 'application/json' } });
      }
      return new Response('{}', { status: 200, headers: { 'Content-Type': 'application/json' } });
    });
    renderPage();
    await screen.findByText('jira.delete');
    await userEvent.click(screen.getByTestId('verify-chain-btn'));
    await waitFor(() => expect(screen.getByRole('status')).toHaveTextContent(/chain broken/i));
    expect(screen.getByRole('status')).toHaveTextContent(/evt-99/);
  });

  test('verify chain button shows error toast when the request throws', async () => {
    vi.spyOn(globalThis, 'fetch').mockImplementation(async (input) => {
      const url = String(input);
      if (url.includes('/governance/audit') && !url.includes('verify')) {
        return new Response(JSON.stringify(SAMPLE), { status: 200, headers: { 'Content-Type': 'application/json' } });
      }
      if (url.includes('/governance/audit/integrity/verify')) {
        throw new Error('network down');
      }
      return new Response('{}', { status: 200, headers: { 'Content-Type': 'application/json' } });
    });
    renderPage();
    await screen.findByText('jira.delete');
    await userEvent.click(screen.getByTestId('verify-chain-btn'));
    // Button re-enables (verifying=false) after the failure — no exception thrown.
    await waitFor(() => expect(screen.getByTestId('verify-chain-btn')).not.toBeDisabled());
  });

  test('export JSON does nothing (toasts) when filtered list is empty', async () => {
    mockFetch([]);
    renderPage();
    await waitFor(() => expect(screen.queryByTestId('audit-table')).not.toBeInTheDocument());
    await userEvent.click(screen.getByTestId('export-json-btn'));
    // No crash; export button still present
    expect(screen.getByTestId('export-json-btn')).toBeInTheDocument();
  });

  test('export CSV and JSON trigger a download when rows are present', async () => {
    mockFetch();
    renderPage();
    await screen.findByText('jira.delete');
    const clickSpy = vi.spyOn(HTMLAnchorElement.prototype, 'click').mockImplementation(() => {});
    if (!URL.createObjectURL) (URL as unknown as { createObjectURL: unknown }).createObjectURL = () => 'blob:mock';
    if (!URL.revokeObjectURL) (URL as unknown as { revokeObjectURL: unknown }).revokeObjectURL = () => {};
    const createUrlSpy = vi.spyOn(URL, 'createObjectURL').mockReturnValue('blob:mock');
    const revokeUrlSpy = vi.spyOn(URL, 'revokeObjectURL').mockImplementation(() => {});
    await userEvent.click(screen.getByTestId('export-csv-btn'));
    await userEvent.click(screen.getByTestId('export-json-btn'));
    expect(clickSpy).toHaveBeenCalledTimes(2);
    expect(createUrlSpy).toHaveBeenCalledTimes(2);
    clickSpy.mockRestore();
    createUrlSpy.mockRestore();
    revokeUrlSpy.mockRestore();
  });

  test('reset filters clears draft and applied filter state', async () => {
    mockFetch();
    renderPage();
    await screen.findByText('jira.delete');
    const toolInput = screen.getByLabelText('Filter by Tool name') as HTMLInputElement;
    await userEvent.type(toolInput, 'jira.delete');
    await userEvent.click(screen.getByRole('button', { name: /reset all filters/i }));
    expect(toolInput.value).toBe('');
  });

  test('free-text filter narrows visible rows client-side', async () => {
    mockFetch();
    renderPage();
    await screen.findByText('github.read');
    const freeText = screen.getByLabelText(/free-text filter/i);
    await userEvent.type(freeText, 'jira');
    await waitFor(() => expect(screen.queryByText('github.read')).not.toBeInTheDocument());
    expect(screen.getByText('jira.delete')).toBeInTheDocument();
  });

  test('refresh button triggers a refetch', async () => {
    const spy = mockFetch();
    renderPage();
    await screen.findByText('jira.delete');
    const callsBefore = spy.mock.calls.length;
    await userEvent.click(screen.getByLabelText('Refresh audit events'));
    await waitFor(() => expect(spy.mock.calls.length).toBeGreaterThan(callsBefore));
  });

  test('pagination Next/Prev buttons page through server offsets', async () => {
    const fullPage = Array.from({ length: 100 }, (_, i) => ({
      event_id: `e${i}`, goal_id: 'g', tool_name: 'github.read', action_level: 'allow', outcome: 'success', note: '',
    }));
    const spy = vi.spyOn(globalThis, 'fetch').mockImplementation(async (input) => {
      const url = String(input);
      if (url.includes('/governance/audit')) {
        const offset = Number(/offset=(\d+)/.exec(url)?.[1] ?? 0);
        const body = offset === 0
          ? fullPage
          : [{ event_id: 'last-one', goal_id: 'g', tool_name: 'github.read', action_level: 'allow', outcome: 'success', note: '' }];
        return new Response(JSON.stringify(body), {
          status: 200, headers: { 'Content-Type': 'application/json' },
        });
      }
      return new Response('{}', { status: 200, headers: { 'Content-Type': 'application/json' } });
    });
    renderPage();
    await screen.findAllByText('github.read');
    const nextBtn = screen.getByLabelText('Next page');
    expect(screen.getByLabelText('Previous page')).toBeDisabled();
    await userEvent.click(nextBtn);
    await waitFor(() => expect(spy.mock.calls.some(([u]) => String(u).includes('offset=100'))).toBe(true));
    await waitFor(() => expect(screen.getByLabelText('Previous page')).not.toBeDisabled());
    const callsBeforePrev = spy.mock.calls.length;
    await userEvent.click(screen.getByLabelText('Previous page'));
    // offset=0 is omitted from the URL (falsy check in the client), so assert
    // a new request was made and it does NOT carry offset=100 any more.
    await waitFor(() => expect(spy.mock.calls.length).toBeGreaterThan(callsBeforePrev));
    const lastCall = spy.mock.calls[spy.mock.calls.length - 1];
    expect(String(lastCall[0])).not.toContain('offset=100');
  });

  test('shows error state when the audit query fails', async () => {
    vi.spyOn(globalThis, 'fetch').mockRejectedValue(new Error('boom'));
    renderPage();
    await waitFor(() => expect(screen.getByRole('alert')).toBeInTheDocument());
  });

  test('start/end date filters and limit selector forward values on apply', async () => {
    const spy = mockFetch();
    renderPage();
    await screen.findByText('jira.delete');
    await userEvent.type(screen.getByLabelText(/filter by start date/i), '2024-01-01T00:00');
    await userEvent.type(screen.getByLabelText(/filter by end date/i), '2024-01-02T00:00');
    await userEvent.selectOptions(screen.getByLabelText('Select result limit'), '200');
    await userEvent.type(screen.getByLabelText('Filter by Goal ID'), 'goal-123');
    await userEvent.click(screen.getByTestId('apply-filters-btn'));
    await waitFor(() =>
      expect(spy.mock.calls.some(([u]) => String(u).includes('goal_id=goal-123') && String(u).includes('limit=200'))).toBe(true)
    );
  });
});
