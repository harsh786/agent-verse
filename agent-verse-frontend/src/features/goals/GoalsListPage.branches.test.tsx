/**
 * Branch companion for GoalsListPage.
 *
 * The existing GoalsListPage.test.tsx focuses on the MissionGoalComposer submit
 * flow. This file covers the goals TABLE and its branches: row rendering, status
 * filter pills + counts, search, the three empty states, the error/loading
 * states, bulk selection + cancel, per-row cancel, sorting, and row navigation.
 *
 * MissionGoalComposer (rendered by the page) fetches /agents; we return [] and
 * feed the goals table via GET /goals.
 */
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter, useLocation } from 'react-router-dom';
import { afterEach, beforeEach, describe, expect, test, vi } from 'vitest';
import { useAuthStore } from '@/stores/auth';
import { GoalsListPage } from './GoalsListPage';

const NOW = new Date().toISOString();

const GOALS = [
  { id: 'g1', goal: 'Deploy to prod', status: 'executing', created_at: NOW, event_count: 3, agent_id: 'agent-xyz', agent_name: 'Deployer', iterations: 2 },
  { id: 'g2', goal: 'Write documentation', status: 'complete', created_at: NOW, event_count: 1 },
  { id: 'g3', goal: 'Analyze failures', status: 'failed', created_at: NOW, event_count: 0 },
];

function mockFetch(opts: { goals?: unknown; goalsStatus?: number } = {}) {
  return vi.spyOn(globalThis, 'fetch').mockImplementation(async (input, init) => {
    const url = String(input);
    const method = (init?.method ?? 'GET').toUpperCase();
    const json = (payload: unknown, status = 200) =>
      new Response(JSON.stringify(payload), { status, headers: { 'Content-Type': 'application/json' } });
    if (url.endsWith('/agents')) return json([]);
    if (/\/goals\/[^/]+\/cancel$/.test(url) && method === 'POST')
      return json({ id: 'g1', status: 'failed', goal: 'Deploy to prod' });
    if (url.match(/\/goals(\?|$)/) && method === 'GET')
      return json(opts.goals ?? { goals: GOALS }, opts.goalsStatus ?? 200);
    return json({});
  });
}

function renderPage(ui?: React.ReactNode) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={qc}>
      <MemoryRouter initialEntries={['/goals']}>
        <GoalsListPage />
        {ui}
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

beforeEach(() => {
  sessionStorage.clear();
  localStorage.clear();
  localStorage.setItem('av_api_key', 'tenant-key');
  useAuthStore.setState({ apiKey: 'tenant-key', tenantId: 'tenant-1', plan: 'free', isAuthenticated: true });
});
afterEach(() => vi.restoreAllMocks());

describe('GoalsListPage — table branches', () => {
  test('renders goal rows with status badges, agent badge and iteration count', async () => {
    mockFetch();
    renderPage();
    expect(await screen.findByText('Deploy to prod')).toBeInTheDocument();
    expect(screen.getByText('Write documentation')).toBeInTheDocument();
    expect(screen.getByText('Analyze failures')).toBeInTheDocument();
    // Status badge inside the g1 row shows 'executing' (the word also appears as a filter pill).
    const g1Row = screen.getByText('Deploy to prod').closest('tr') as HTMLElement;
    expect(within(g1Row).getByText('executing')).toBeInTheDocument();
    // Agent badge shows agent_name
    expect(screen.getByText(/Deployer/)).toBeInTheDocument();
    // Iteration count
    expect(screen.getByText(/2 iters/)).toBeInTheDocument();
  });

  test('status filter pills carry counts and filter the table', async () => {
    mockFetch();
    renderPage();
    await screen.findByText('Deploy to prod');
    // The "complete" filter pill shows a count of 1.
    const completePill = screen.getByRole('button', { name: /^complete/i });
    expect(completePill).toHaveTextContent('1');
    await userEvent.click(completePill);
    await waitFor(() => expect(screen.queryByText('Deploy to prod')).not.toBeInTheDocument());
    expect(screen.getByText('Write documentation')).toBeInTheDocument();
  });

  test('search narrows the visible rows', async () => {
    mockFetch();
    renderPage();
    await screen.findByText('Deploy to prod');
    await userEvent.type(screen.getByLabelText(/Search goals/i), 'Deploy');
    await waitFor(() => expect(screen.queryByText('Write documentation')).not.toBeInTheDocument());
    expect(screen.getByText('Deploy to prod')).toBeInTheDocument();
  });

  test('shows the "no goals yet" empty state when the tenant has none', async () => {
    mockFetch({ goals: { goals: [] } });
    renderPage();
    expect(await screen.findByText(/No goals yet/i)).toBeInTheDocument();
    expect(screen.getByText(/Submit your first goal to get started/i)).toBeInTheDocument();
  });

  test('shows the friendly failed-filter empty state when no failed goals exist', async () => {
    mockFetch({ goals: { goals: [GOALS[0], GOALS[1]] } }); // no failed goal
    renderPage();
    await screen.findByText('Deploy to prod');
    await userEvent.click(screen.getByRole('button', { name: /^failed/i }));
    expect(await screen.findByText(/No failed goals/i)).toBeInTheDocument();
    expect(screen.getByText(/All goals running smoothly!/i)).toBeInTheDocument();
    // "Show all goals" resets the filter.
    await userEvent.click(screen.getByRole('button', { name: /Show all goals/i }));
    expect(await screen.findByText('Deploy to prod')).toBeInTheDocument();
  });

  test('shows the no-match search empty state and clears it', async () => {
    mockFetch();
    renderPage();
    await screen.findByText('Deploy to prod');
    await userEvent.type(screen.getByLabelText(/Search goals/i), 'zzzznomatch');
    expect(await screen.findByText(/No goals match your search/i)).toBeInTheDocument();
    await userEvent.click(screen.getByRole('button', { name: /Clear search/i }));
    expect(await screen.findByText('Deploy to prod')).toBeInTheDocument();
  });

  test('renders an error state when the goals request fails', async () => {
    mockFetch({ goalsStatus: 500 });
    renderPage();
    expect(await screen.findByText(/Failed to load goals/i)).toBeInTheDocument();
  });

  test('bulk selection reveals the toolbar and cancels selected running goals', async () => {
    const spy = mockFetch();
    renderPage();
    await screen.findByText('Deploy to prod');
    await userEvent.click(screen.getByLabelText(/Select all goals on page/i));
    expect(await screen.findByText(/3 selected/i)).toBeInTheDocument();
    await userEvent.click(screen.getByRole('button', { name: /Cancel All/i }));
    // Only the executing goal (g1) is eligible for bulk cancel.
    await waitFor(() =>
      expect(
        spy.mock.calls.some(
          ([u, i]) => String(u).includes('/goals/g1/cancel') && (i as RequestInit)?.method === 'POST',
        ),
      ).toBe(true),
    );
    await waitFor(() => expect(screen.queryByText(/3 selected/i)).not.toBeInTheDocument());
  });

  test('per-row cancel button posts a cancel for a running goal', async () => {
    const spy = mockFetch();
    renderPage();
    await screen.findByText('Deploy to prod');
    // Only executing/planning goals get a cancel button; g1 is executing.
    const cancelButtons = screen.getAllByRole('button', { name: /Cancel goal/i });
    expect(cancelButtons).toHaveLength(1);
    await userEvent.click(cancelButtons[0]);
    await waitFor(() =>
      expect(
        spy.mock.calls.some(
          ([u, i]) => String(u).includes('/goals/g1/cancel') && (i as RequestInit)?.method === 'POST',
        ),
      ).toBe(true),
    );
  });

  test('clicking the Goal header sorts rows alphabetically ascending', async () => {
    mockFetch();
    renderPage();
    await screen.findByText('Deploy to prod');
    await userEvent.click(screen.getByRole('columnheader', { name: /^Goal/ }));
    await waitFor(() => {
      const rows = screen.getAllByRole('row').slice(1); // drop header row
      const firstCellText = within(rows[0]).getByText(/Deploy to prod|Write documentation|Analyze failures/);
      // Ascending: "Analyze failures" sorts first.
      expect(firstCellText).toHaveTextContent('Analyze failures');
    });
  });

  test('clicking a row navigates to the goal detail route', async () => {
    mockFetch();
    function Loc() {
      const loc = useLocation();
      return <span data-testid="loc">{loc.pathname}</span>;
    }
    renderPage(<Loc />);
    await userEvent.click(await screen.findByText('Deploy to prod'));
    await waitFor(() => expect(screen.getByTestId('loc')).toHaveTextContent('/goals/g1'));
  });

  test('shows the loading skeleton before goals resolve', () => {
    vi.spyOn(globalThis, 'fetch').mockImplementation(async (input) => {
      const url = String(input);
      if (url.endsWith('/agents'))
        return new Response('[]', { status: 200, headers: { 'Content-Type': 'application/json' } });
      // Never resolve the goals list.
      return new Promise(() => {}) as unknown as Response;
    });
    renderPage();
    // Subtitle is always present; rows are not yet rendered.
    expect(screen.getByText(/Submit and track autonomous agent goals/i)).toBeInTheDocument();
    expect(screen.queryByText('Deploy to prod')).not.toBeInTheDocument();
  });
});
