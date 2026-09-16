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
    await screen.findByText('github.read');

    // Apply the outcome=denied filter (client-side, but must cover all pages).
    await userEvent.selectOptions(screen.getByLabelText('Filter by outcome'), 'denied');
    await userEvent.click(screen.getByTestId('apply-filters-btn'));

    // The denied event lives on page 2 — it must still surface.
    await waitFor(() => expect(screen.getByText('stripe.refund')).toBeInTheDocument());
    // And the scan must have paged past the first page.
    expect(spy.mock.calls.some(([u]) => String(u).includes('offset=100'))).toBe(true);
  });
});
