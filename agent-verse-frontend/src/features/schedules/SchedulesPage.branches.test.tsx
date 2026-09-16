import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter } from 'react-router-dom';
import { afterEach, beforeEach, describe, expect, test, vi } from 'vitest';
import { useAuthStore } from '@/stores/auth';
import { SchedulesPage } from './SchedulesPage';

// Companion suite to SchedulesPage.test.tsx — targets UNTESTED branches:
// populated table, pause/resume/fire/delete flows (asserting endpoint + method),
// bulk operations, analytics rendering, advisor→create prefill, NL create,
// and the run-history drawer. It must not overlap/duplicate the base file.

const SCHEDULES = [
  { schedule_id: 'sched-active', goal_template: 'Daily report', trigger_type: 'cron', cron_expr: '0 9 * * *', paused: false },
  { schedule_id: 'sched-paused', goal_template: 'Weekly summary', trigger_type: 'interval', interval_seconds: 3600, paused: true },
  { schedule_id: 'sched-hook', goal_template: 'On webhook', trigger_type: 'webhook', paused: false },
];

const ANALYTICS = {
  total: 5,
  active: 3,
  paused: 2,
  by_trigger_type: { cron: 3, interval: 2 },
  fired_last_7_days: { '2026-09-10': 1, '2026-09-11': 4 },
  schedules_summary: [
    { schedule_id: 's1', goal_template: 'Overview goal', trigger_type: 'cron', paused: false, last_fired_at: '2026-09-11T00:00:00Z' },
  ],
};

function json(body: unknown, status = 200) {
  return new Response(JSON.stringify(body), { status, headers: { 'Content-Type': 'application/json' } });
}

function mockFetch(opts: { schedules?: unknown[]; schedulesPending?: boolean } = {}) {
  return vi.spyOn(globalThis, 'fetch').mockImplementation(async (input, init) => {
    const url = String(input);
    const method = (init?.method ?? 'GET').toUpperCase();
    if (url.includes('/schedules/suggest'))
      return json({
        suggestions: [
          { rank: 1, title: 'Daily at 9', trigger_type: 'cron', cron_expr: '0 9 * * *', interval_seconds: null, rationale: 'Best for daily digests', use_case: 'daily reports' },
        ],
        llm_powered: true,
      });
    if (url.includes('/schedules/analytics')) return json(ANALYTICS);
    if (url.includes('/nl/schedule') && method === 'POST')
      return json([{ schedule_id: 'sc-1', name: 'PR review', trigger_type: 'cron', cron_expr: '0 9 * * 1-5' }]);
    if (/\/schedules\/[^/]+\/history/.test(url))
      return json({ total: 2, runs: [
        { run_id: 'r1', started_at: '2026-09-11T10:00:00Z', status: 'success', duration_ms: 1200, goal_id: 'g1' },
        { run_id: 'r2', started_at: '2026-09-11T09:00:00Z', status: 'failed', duration_ms: 500, error: 'boom' },
      ] });
    if (/\/schedules\/[^/]+\/pause/.test(url) && method === 'POST') return json({ ok: true });
    if (/\/schedules\/[^/]+\/resume/.test(url) && method === 'POST') return json({ ok: true });
    if (/\/schedules\/[^/]+\/fire/.test(url) && method === 'POST') return json({ ok: true });
    if (/\/schedules\/[^/]+$/.test(url) && method === 'DELETE') return json({ ok: true });
    if (url.includes('/schedules') && method === 'POST') return json({ schedule_id: 'new-sched' }, 201);
    if (url.includes('/schedules')) {
      if (opts.schedulesPending) return new Promise<Response>(() => {});
      return json(opts.schedules ?? SCHEDULES);
    }
    if (url.includes('/agents')) return json([]);
    return json({});
  });
}

function renderPage() {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={qc}>
      <MemoryRouter>
        <SchedulesPage />
      </MemoryRouter>
    </QueryClientProvider>
  );
}

beforeEach(() => {
  localStorage.clear();
  useAuthStore.setState({ apiKey: 'tenant-key', tenantId: 'tenant-1', plan: 'free', isAuthenticated: true });
});
afterEach(() => vi.restoreAllMocks());

describe('SchedulesPage branches', () => {
  test('renders the populated schedules table with each row', async () => {
    mockFetch();
    renderPage();
    expect(await screen.findByTestId('schedules-table')).toBeInTheDocument();
    expect(screen.getByText('Daily report')).toBeInTheDocument();
    expect(screen.getByText('Weekly summary')).toBeInTheDocument();
    expect(screen.getByTestId('schedule-row-sched-active')).toBeInTheDocument();
  });

  test('empty schedules list shows the no-schedules hint', async () => {
    mockFetch({ schedules: [] });
    renderPage();
    await screen.findByRole('heading', { name: /schedules/i });
    expect(await screen.findByText(/No schedules yet/i)).toBeInTheDocument();
    expect(screen.queryByTestId('schedules-table')).not.toBeInTheDocument();
  });

  test('pause button POSTs to the pause endpoint', async () => {
    const spy = mockFetch();
    renderPage();
    await userEvent.click(await screen.findByTestId('pause-btn-sched-active'));
    await waitFor(() =>
      expect(spy.mock.calls.some(([u, i]) =>
        String(u).includes('/schedules/sched-active/pause') && (i as RequestInit)?.method === 'POST',
      )).toBe(true),
    );
  });

  test('resume button POSTs to the resume endpoint for a paused schedule', async () => {
    const spy = mockFetch();
    renderPage();
    await userEvent.click(await screen.findByTestId('resume-btn-sched-paused'));
    await waitFor(() =>
      expect(spy.mock.calls.some(([u, i]) =>
        String(u).includes('/schedules/sched-paused/resume') && (i as RequestInit)?.method === 'POST',
      )).toBe(true),
    );
  });

  test('run-now button fires a webhook schedule', async () => {
    const spy = mockFetch();
    renderPage();
    await userEvent.click(await screen.findByTestId('run-now-btn-sched-hook'));
    await waitFor(() =>
      expect(spy.mock.calls.some(([u, i]) =>
        String(u).includes('/schedules/sched-hook/fire') && (i as RequestInit)?.method === 'POST',
      )).toBe(true),
    );
  });

  test('deleting a schedule confirms then sends DELETE', async () => {
    const spy = mockFetch();
    renderPage();
    await userEvent.click(await screen.findByTestId('delete-btn-sched-active'));
    const dialog = await screen.findByRole('dialog');
    await userEvent.click(within(dialog).getByRole('button', { name: 'Delete' }));
    await waitFor(() =>
      expect(spy.mock.calls.some(([u, i]) =>
        String(u).includes('/schedules/sched-active') && (i as RequestInit)?.method === 'DELETE',
      )).toBe(true),
    );
  });

  test('bulk select then bulk pause posts a pause for every selected schedule', async () => {
    const spy = mockFetch();
    renderPage();
    await screen.findByTestId('schedules-table');
    // The first checkbox is the header "select all".
    await userEvent.click(screen.getAllByRole('checkbox')[0]);
    const toolbar = screen.getByText(/3 selected/i).closest('div') as HTMLElement;
    await userEvent.click(within(toolbar).getByRole('button', { name: 'Pause' }));
    await waitFor(() => {
      const pauseCalls = spy.mock.calls.filter(([u, i]) =>
        /\/schedules\/[^/]+\/pause/.test(String(u)) && (i as RequestInit)?.method === 'POST');
      expect(pauseCalls.length).toBe(3);
    });
  });

  test('bulk delete confirms then DELETEs every selected schedule', async () => {
    const spy = mockFetch();
    renderPage();
    await screen.findByTestId('schedules-table');
    await userEvent.click(screen.getAllByRole('checkbox')[0]);
    const toolbar = screen.getByText(/3 selected/i).closest('div') as HTMLElement;
    await userEvent.click(within(toolbar).getByRole('button', { name: 'Delete' }));
    const dialog = await screen.findByRole('dialog');
    await userEvent.click(within(dialog).getByRole('button', { name: 'Delete' }));
    await waitFor(() => {
      const delCalls = spy.mock.calls.filter(([u, i]) =>
        /\/schedules\/[^/]+$/.test(String(u)) && (i as RequestInit)?.method === 'DELETE');
      expect(delCalls.length).toBe(3);
    });
  });

  test('analytics tab renders KPIs, charts and the schedule overview', async () => {
    mockFetch();
    renderPage();
    await screen.findByRole('heading', { name: /schedules/i });
    await userEvent.click(screen.getByTestId('tab-analytics'));
    expect(await screen.findByText('Total')).toBeInTheDocument();
    expect(screen.getByText(/Firing Activity/i)).toBeInTheDocument();
    expect(screen.getByText(/Trigger Types/i)).toBeInTheDocument();
    expect(screen.getByText(/Schedule Overview/i)).toBeInTheDocument();
    expect(screen.getByText('Overview goal')).toBeInTheDocument();
  });

  test('advisor suggestions render and "Use this schedule" prefills the create form', async () => {
    mockFetch();
    renderPage();
    await screen.findByRole('heading', { name: /schedules/i });
    await userEvent.click(screen.getByTestId('tab-advisor'));
    await userEvent.type(await screen.findByPlaceholderText(/Describe what your schedule should do/i), 'daily digest');
    await userEvent.click(screen.getByTestId('suggest-btn'));
    expect(await screen.findByText('Daily at 9')).toBeInTheDocument();
    expect(screen.getByText(/AI-powered/i)).toBeInTheDocument();
    await userEvent.click(screen.getByRole('button', { name: /Use this schedule/i }));
    // Switches to Schedules tab and auto-opens the create form prefilled with the use_case.
    const form = await screen.findByTestId('create-schedule-form');
    expect(within(form).getByPlaceholderText(/Describe what to run/i)).toHaveValue('daily reports');
  });

  test('NL scheduler creates a schedule and echoes the assistant reply', async () => {
    const spy = mockFetch();
    renderPage();
    await screen.findByRole('heading', { name: /schedules/i });
    await userEvent.click(screen.getByTestId('tab-nl'));
    const input = await screen.findByPlaceholderText(/plain English/i);
    await userEvent.type(input, 'Run a PR review every weekday at 9 AM');
    await userEvent.click(screen.getByRole('button', { name: 'Create' }));
    await waitFor(() =>
      expect(spy.mock.calls.some(([u, i]) =>
        String(u).includes('/nl/schedule') && (i as RequestInit)?.method === 'POST',
      )).toBe(true),
    );
    expect(await screen.findByText(/Created schedule/i)).toBeInTheDocument();
    expect(screen.getByText('Run a PR review every weekday at 9 AM')).toBeInTheDocument();
  });

  test('clicking a schedule row opens the run-history drawer with runs', async () => {
    mockFetch();
    renderPage();
    await screen.findByTestId('schedules-table');
    await userEvent.click(screen.getByText('Daily report'));
    expect(await screen.findByText('Run History')).toBeInTheDocument();
    // "Succeeded"/"Failed" appear both on the run card and in the footer stats.
    expect((await screen.findAllByText('Succeeded')).length).toBeGreaterThan(0);
    expect(screen.getAllByText('Failed').length).toBeGreaterThan(0);
    expect(screen.getByText('boom')).toBeInTheDocument();
  });
});
