import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { render, screen, waitFor, fireEvent } from '@testing-library/react';
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

    if (url.includes('/api/v1/workflows/runs')) return ok(plan.runs ?? []);
    if (/\/api\/v1\/workflows\/[^/]+\/(trigger|pause|resume)/.test(url) && method === 'POST')
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

  test('pausing an active workflow POSTs to the pause endpoint', async () => {
    const spy = mockFetch({ workflows: [wf()] });
    renderPage();
    await screen.findByText('Nightly Report');
    fireEvent.click(screen.getByTitle('Pause'));
    await waitFor(() =>
      expect(
        spy.mock.calls.some(([u, i]) =>
          /\/api\/v1\/workflows\/wf-1\/pause/.test(String(u)) && (i as RequestInit)?.method === 'POST',
        ),
      ).toBe(true),
    );
  });

  test('a paused workflow offers a Resume toggle that hits the resume endpoint', async () => {
    const spy = mockFetch({ workflows: [wf({ status: 'paused', success_rate: 0.5 })] });
    renderPage();
    await screen.findByText('Nightly Report');
    // Paused status label and the sub-0.8 success rate styling path.
    expect(screen.getByText('Paused')).toBeInTheDocument();
    expect(screen.getByText('50% success')).toBeInTheDocument();
    fireEvent.click(screen.getByTitle('Resume'));
    await waitFor(() =>
      expect(
        spy.mock.calls.some(([u]) => /\/api\/v1\/workflows\/wf-1\/resume/.test(String(u))),
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
