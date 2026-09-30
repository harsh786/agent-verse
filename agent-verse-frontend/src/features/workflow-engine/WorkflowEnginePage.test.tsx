import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { render, screen, waitFor, fireEvent, within } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { afterEach, beforeEach, describe, expect, test, vi } from 'vitest';
import { useAuthStore } from '@/stores/auth';
import { WorkflowEnginePage } from './WorkflowEnginePage';

function wf(overrides: Record<string, unknown> = {}) {
  return {
    id: 'wf-1',
    name: 'Nightly Report',
    description: 'Runs every night',
    status: 'active',
    trigger_type: 'cron',
    run_count: 1234,
    success_rate: 0.92,
    last_run_at: '2026-01-01T00:00:00Z',
    created_at: '2026-01-01T00:00:00Z',
    ...overrides,
  };
}

function run(overrides: Record<string, unknown> = {}) {
  return {
    run_id: 'run-abcdef123456',
    workflow_id: 'wf-1',
    status: 'completed',
    started_at: '2026-01-01T00:00:00Z',
    duration_ms: 2500,
    ...overrides,
  };
}

interface FetchPlan {
  workflows?: unknown;
  runs?: unknown;
}

function mockFetch(plan: FetchPlan = {}) {
  return vi.spyOn(globalThis, 'fetch').mockImplementation(async (input, init) => {
    const url = String(input);
    const method = (init?.method ?? 'GET').toUpperCase();
    const ok = (body: unknown) =>
      new Response(JSON.stringify(body), { status: 200, headers: { 'Content-Type': 'application/json' } });

    if (url.includes('/api/v1/runs')) return ok(plan.runs ?? []);
    if (/\/api\/v1\/workflows\/[^/]+\/(trigger|publish|unpublish)/.test(url) && method === 'POST')
      return ok({ status: 'ok' });
    if (url.includes('/api/v1/workflows')) return ok(plan.workflows ?? []);
    return ok({});
  });
}

function renderPage() {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={qc}>
      <MemoryRouter><WorkflowEnginePage /></MemoryRouter>
    </QueryClientProvider>,
  );
}

beforeEach(() => {
  useAuthStore.setState({ apiKey: 'k', tenantId: 't', plan: 'free', isAuthenticated: true });
});
afterEach(() => vi.restoreAllMocks());

describe('WorkflowEnginePage', () => {
  test('renders the header and description', async () => {
    mockFetch({ workflows: [] });
    renderPage();
    expect(await screen.findByRole('heading', { name: /Workflow Engine/i })).toBeInTheDocument();
    expect(screen.getByText(/Event-driven workflow automation/i)).toBeInTheDocument();
  });

  test('shows the empty state when no workflows exist', async () => {
    mockFetch({ workflows: [] });
    renderPage();
    expect(await screen.findByText(/No workflows configured/i)).toBeInTheDocument();
    expect(screen.getByText(/Create workflows in the Workflow Builder/i)).toBeInTheDocument();
  });

  test('renders a workflow card with status, run count and success rate', async () => {
    mockFetch({ workflows: [wf()] });
    renderPage();
    expect(await screen.findByText('Nightly Report')).toBeInTheDocument();
    expect(screen.getByText('Active')).toBeInTheDocument();
    expect(screen.getByText('1,234 runs')).toBeInTheDocument();
    expect(screen.getByText('92% success')).toBeInTheDocument();
    // KPI row: 1 active workflow.
    expect(screen.getByText('Active Workflows')).toBeInTheDocument();
  });

  test('triggering a run POSTs to the trigger endpoint and switches to the runs tab', async () => {
    const spy = mockFetch({ workflows: [wf()], runs: [run()] });
    renderPage();
    await screen.findByText('Nightly Report');
    fireEvent.click(screen.getByRole('button', { name: /^Run$/i }));
    await waitFor(() =>
      expect(
        spy.mock.calls.some(([u, i]) =>
          /\/api\/v1\/workflows\/wf-1\/trigger/.test(String(u)) && (i as RequestInit)?.method === 'POST',
        ),
      ).toBe(true),
    );
    // onSuccess flips to the Run History tab, which then loads the runs.
    await waitFor(() => expect(screen.getByText(/run-abcdef12/i)).toBeInTheDocument());
  });

  test('pausing an active workflow unpublishes it (the real route)', async () => {
    const spy = mockFetch({ workflows: [wf()] });
    renderPage();
    await screen.findByText('Nightly Report');
    fireEvent.click(screen.getByTitle('Pause'));
    await waitFor(() =>
      expect(
        spy.mock.calls.some(([u, i]) =>
          /\/api\/v1\/workflows\/wf-1\/unpublish$/.test(String(u)) && (i as RequestInit)?.method === 'POST',
        ),
      ).toBe(true),
    );
    // The non-existent /workflows/{id}/pause route is never called.
    expect(spy.mock.calls.some(([u]) => /\/workflows\/wf-1\/(pause|resume)/.test(String(u)))).toBe(false);
  });

  test('a published workflow (backend status) renders as Active', async () => {
    mockFetch({ workflows: [wf({ status: 'published' })] });
    renderPage();
    await screen.findByText('Nightly Report');
    expect(screen.getByText('Active')).toBeInTheDocument();
    expect(screen.getByTitle('Pause')).toBeInTheDocument();
  });

  test('trigger sends the required JSON body', async () => {
    const spy = mockFetch({ workflows: [wf()], runs: [run()] });
    renderPage();
    await screen.findByText('Nightly Report');
    fireEvent.click(screen.getByRole('button', { name: /^Run$/i }));
    await waitFor(() => {
      const call = spy.mock.calls.find(([u]) => /\/wf-1\/trigger/.test(String(u)));
      expect(call).toBeTruthy();
      expect(JSON.parse(String((call![1] as RequestInit).body))).toEqual({ inputs: {} });
    });
  });

  test('a paused workflow offers a Resume toggle that publishes it', async () => {
    const spy = mockFetch({ workflows: [wf({ status: 'paused', success_rate: 0.5 })] });
    renderPage();
    await screen.findByText('Nightly Report');
    // Paused status label and the sub-0.8 success rate styling path.
    expect(screen.getByText('Paused')).toBeInTheDocument();
    expect(screen.getByText('50% success')).toBeInTheDocument();
    fireEvent.click(screen.getByTitle('Resume'));
    await waitFor(() =>
      expect(
        spy.mock.calls.some(([u]) => /\/api\/v1\/workflows\/wf-1\/publish$/.test(String(u))),
      ).toBe(true),
    );
  });

  test('renders a "Load more" control when the total exceeds the page and grows the window', async () => {
    const many = Array.from({ length: 30 }, (_, i) => wf({ id: `wf-${i}`, name: `WF ${i}` }));
    const spy = mockFetch({ workflows: { items: many, total: 55 } });
    renderPage();
    await screen.findByText('WF 0');
    const loadMore = await screen.findByRole('button', { name: /Load more \(30 of 55\)/i });
    fireEvent.click(loadMore);
    // A larger per_page window is requested after clicking Load more.
    await waitFor(() =>
      expect(spy.mock.calls.some(([u]) => String(u).includes('per_page=60'))).toBe(true),
    );
  });

  test('switching to the Run History tab shows the empty run state', async () => {
    mockFetch({ workflows: [wf()], runs: [] });
    renderPage();
    await screen.findByText('Nightly Report');
    fireEvent.click(screen.getByRole('button', { name: /Run History/i }));
    expect(await screen.findByText(/No run history yet/i)).toBeInTheDocument();
    expect(screen.getByText(/Trigger a workflow to see runs here/i)).toBeInTheDocument();
  });

  test('the Run History tab lists runs with a short id, status and formatted duration', async () => {
    mockFetch({
      workflows: [wf()],
      runs: [
        run({ run_id: 'run-completed01', status: 'completed', duration_ms: 2500 }),
        run({ run_id: 'run-failed00002', status: 'failed', duration_ms: 500 }),
        run({ run_id: 'run-running0003', status: 'running', duration_ms: undefined }),
      ],
    });
    renderPage();
    await screen.findByText('Nightly Report');
    fireEvent.click(screen.getByRole('button', { name: /Run History/i }));
    // 12-char truncated id.
    expect(await screen.findByText(/run-complete/i)).toBeInTheDocument();
    // Seconds-formatted and ms-formatted durations both appear.
    expect(screen.getByText(/· 2\.5s/)).toBeInTheDocument();
    expect(screen.getByText(/· 500ms/)).toBeInTheDocument();
    // KPI reflects the one failed and one running run.
    expect(screen.getByText('Running Now')).toBeInTheDocument();
    expect(screen.getByText('Failed (24h)')).toBeInTheDocument();
  });

  test('normalises a paginated runs envelope and shows its Load more control', async () => {
    const runsEnvelope = {
      items: Array.from({ length: 20 }, (_, i) => run({ run_id: `run-${i}pad000000` })),
      total: 40,
    };
    mockFetch({ workflows: [wf()], runs: runsEnvelope });
    renderPage();
    await screen.findByText('Nightly Report');
    fireEvent.click(screen.getByRole('button', { name: /Run History/i }));
    expect(await screen.findByRole('button', { name: /Load more \(20 of 40\)/i })).toBeInTheDocument();
  });
});

// UI-WF-ENGINE: against the real backend the tab was mostly non-functional —
// a freshly triggered run is `pending`, which had no status style and crashed
// the page ("Cannot read properties of undefined (reading 'dot')"); runs were
// requested with `limit` (ignored — the route takes per_page); workflows could
// not be opened; and trigger failures were silent.
describe('WorkflowEnginePage — view, select and run (UI-WF-ENGINE)', () => {
  const detail = {
    ...wf({ status: 'published', trigger_type: 'api' }),
    version: 3,
    labels: {},
    updated_at: '2026-01-02T00:00:00Z',
    access: 'admin',
    definition: {
      name: 'Nightly Report',
      trigger: { type: 'api' },
      steps: [
        { id: 'fetch', type: 'http' },
        { id: 'summarise', type: 'llm_prompt', depends_on: ['fetch'] },
      ],
    },
  };

  // Mirrors the real routes: POST trigger → 202 {run_id, workflow_id, status:'pending'},
  // GET /api/v1/runs?per_page=…[&workflow_id=…] → {items,total,page,per_page}.
  function backend(opts: { triggerStatus?: number; triggerBody?: unknown } = {}) {
    const runs: Record<string, unknown>[] = [];
    const spy = vi.spyOn(globalThis, 'fetch').mockImplementation(async (input, init) => {
      const url = String(input);
      const method = (init?.method ?? 'GET').toUpperCase();
      const json = (body: unknown, status = 200) =>
        new Response(JSON.stringify(body), { status, headers: { 'Content-Type': 'application/json' } });
      if (/\/api\/v1\/workflows\/wf-1\/trigger$/.test(url) && method === 'POST') {
        if (opts.triggerStatus && opts.triggerStatus >= 400)
          return json(opts.triggerBody ?? { detail: 'boom' }, opts.triggerStatus);
        const r = { run_id: 'run-new-000001', workflow_id: 'wf-1', status: 'pending', started_at: null };
        runs.unshift({ ...r, workflow_name: 'Nightly Report', started_at: '2026-01-03T00:00:00Z' });
        return json(r, 202);
      }
      if (url.includes('/api/v1/runs')) {
        const u = new URL(url);
        const wfId = u.searchParams.get('workflow_id');
        const items = wfId ? runs.filter((r) => r.workflow_id === wfId) : runs;
        return json({ items, total: items.length, page: 1, per_page: Number(u.searchParams.get('per_page') ?? 20) });
      }
      if (/\/api\/v1\/workflows\/wf-1$/.test(url)) return json(detail);
      if (url.includes('/api/v1/workflows'))
        return json({ items: [wf({ status: 'published', trigger_type: 'api' })], total: 1, page: 1, per_page: 30 });
      return json({});
    });
    return { spy, runs };
  }

  test('running a workflow shows the pending run (no crash) with a link to its detail page', async () => {
    backend();
    renderPage();
    await screen.findByText('Nightly Report');
    fireEvent.click(screen.getByRole('button', { name: /^Run$/i }));
    expect(await screen.findByText(/run started/i)).toBeInTheDocument();
    expect(screen.getByRole('link', { name: /view run/i })).toHaveAttribute(
      'href', '/workflows/wf-1/runs/run-new-000001',
    );
    expect(await screen.findByText('Pending')).toBeInTheDocument();
    expect(screen.getByRole('heading', { name: /Workflow Engine/i })).toBeInTheDocument();
  });

  test('unknown run statuses render with a neutral badge instead of crashing', async () => {
    mockFetch({ workflows: [wf()], runs: [run({ run_id: 'run-waiting0001', status: 'waiting' })] });
    renderPage();
    await screen.findByText('Nightly Report');
    fireEvent.click(screen.getByRole('button', { name: /Run History/i }));
    expect(await screen.findByText(/run-waiting0/i)).toBeInTheDocument();
    expect(screen.getByText('Waiting')).toBeInTheDocument();
  });

  test("the backend's run statuses (complete, waiting_hitl, timed_out) get proper labels", async () => {
    mockFetch({
      workflows: [wf()],
      runs: [
        run({ run_id: 'run-complete001', status: 'complete' }),
        run({ run_id: 'run-hitl0000001', status: 'waiting_hitl' }),
        run({ run_id: 'run-timeout0001', status: 'timed_out' }),
      ],
    });
    renderPage();
    await screen.findByText('Nightly Report');
    fireEvent.click(screen.getByRole('button', { name: /Run History/i }));
    expect(await screen.findByText('Completed')).toBeInTheDocument();
    expect(screen.getByText('Awaiting approval')).toBeInTheDocument();
    expect(screen.getByText('Timed out')).toBeInTheDocument();
  });

  test('runs are requested with per_page (the route ignores limit) and link to the run detail', async () => {
    const spy = mockFetch({ workflows: [wf()], runs: [run()] });
    renderPage();
    await screen.findByText('Nightly Report');
    fireEvent.click(screen.getByRole('button', { name: /Run History/i }));
    const link = await screen.findByRole('link', { name: /run-abcdef12/i });
    expect(link).toHaveAttribute('href', '/workflows/wf-1/runs/run-abcdef123456');
    const runsCalls = spy.mock.calls.map(([u]) => String(u)).filter((u) => u.includes('/api/v1/runs'));
    expect(runsCalls.length).toBeGreaterThan(0);
    expect(runsCalls.every((u) => /per_page=\d+/.test(u) && !/[?&]limit=/.test(u))).toBe(true);
  });

  test('selecting a workflow shows its definition, links and its own runs', async () => {
    const { spy } = backend();
    renderPage();
    fireEvent.click(await screen.findByRole('button', { name: /view nightly report/i }));
    const panel = await screen.findByRole('region', { name: /nightly report details/i });
    expect(await within(panel).findByText('fetch')).toBeInTheDocument();
    expect(within(panel).getByText('summarise')).toBeInTheDocument();
    expect(within(panel).getByText('llm_prompt')).toBeInTheDocument();
    expect(within(panel).getByText(/v3/)).toBeInTheDocument();
    expect(within(panel).getByRole('link', { name: /open in builder/i })).toHaveAttribute(
      'href', '/workflows/wf-1/edit',
    );
    expect(within(panel).getByRole('link', { name: /all runs/i })).toHaveAttribute(
      'href', '/workflows/wf-1/runs',
    );
    expect(within(panel).getByText(/no runs yet/i)).toBeInTheDocument();
    await waitFor(() =>
      expect(spy.mock.calls.some(([u]) => /\/api\/v1\/runs\?.*workflow_id=wf-1/.test(String(u)))).toBe(true),
    );
    // Run from the panel: the new run appears in the panel's run list.
    fireEvent.click(within(panel).getByRole('button', { name: /run now/i }));
    expect(await within(panel).findByRole('link', { name: /run-new-0000/i })).toHaveAttribute(
      'href', '/workflows/wf-1/runs/run-new-000001',
    );
  });

  test('a refused trigger shows the server reason', async () => {
    backend({ triggerStatus: 503, triggerBody: { detail: 'Workflow runner not available' } });
    renderPage();
    await screen.findByText('Nightly Report');
    fireEvent.click(screen.getByRole('button', { name: /^Run$/i }));
    expect(await screen.findByRole('alert')).toHaveTextContent(/workflow runner not available/i);
  });
});
