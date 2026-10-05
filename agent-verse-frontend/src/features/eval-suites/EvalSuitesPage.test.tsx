/**
 * EvalSuitesPage tests.
 *
 * The page loads suites from GET /intelligence/eval-suites (via apiFetch),
 * renders KPI totals + suite cards, runs a suite (POST .../run), and creates a
 * suite (POST /intelligence/eval-suites). Covers loading, error, empty, list,
 * and both mutation flows with real endpoint assertions.
 */
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter } from 'react-router-dom';
import { afterEach, beforeEach, describe, expect, test, vi } from 'vitest';
import { useAuthStore } from '@/stores/auth';
import { EvalSuitesPage } from './EvalSuitesPage';

// The real GET /intelligence/eval-suites shape: suite_id + the newest run.
const SUITES = [
  {
    suite_id: 'suite-1',
    name: 'Q1 Quality Benchmarks',
    description: 'Baseline quality checks.',
    task_count: 12,
    dataset_version: 3,
    created_at: '2025-12-01T00:00:00Z',
    last_run: {
      run_id: 'r-9', status: 'completed', total: 12, passed: 10, failed: 2, pass_rate: 0.83,
      run_at: '2026-01-01T00:00:00Z', dataset_version: 3,
    },
  },
  {
    suite_id: 'suite-2',
    name: 'Regression Guard',
    task_count: 5,
    dataset_version: 1,
    created_at: '2025-12-02T00:00:00Z',
    last_run: {
      run_id: 'r-10', status: 'running', total: 5, passed: 0, failed: 0, dataset_version: 1,
      run_at: '2026-01-02T00:00:00Z',
      progress: { total: 5, done: 2, passed: 1, failed: 1, unscored: 0, running: 2, pending: 1 },
    },
  },
  {
    suite_id: 'suite-3',
    name: 'Stalled Suite',
    task_count: 4,
    dataset_version: 1,
    created_at: '2025-12-03T00:00:00Z',
    last_run: { run_id: 'r-11', status: 'abandoned', total: 4, passed: 0, failed: 0 },
  },
  {
    suite_id: 'suite-4',
    name: 'Never Run',
    task_count: 2,
    dataset_version: 1,
    created_at: '2025-12-04T00:00:00Z',
    last_run: null,
  },
];

function jsonResponse(body: unknown, status = 200) {
  return new Response(typeof body === 'string' ? body : JSON.stringify(body), {
    status,
    headers: { 'Content-Type': 'application/json' },
  });
}

function renderPage() {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={qc}>
      <MemoryRouter>
        <EvalSuitesPage />
      </MemoryRouter>
    </QueryClientProvider>
  );
}

describe('EvalSuitesPage', () => {
  beforeEach(() => {
    useAuthStore.setState({ apiKey: 'k', tenantId: 't', plan: 'free', isAuthenticated: true });
  });
  afterEach(() => vi.restoreAllMocks());

  test('renders the heading and New Suite button', async () => {
    vi.spyOn(globalThis, 'fetch').mockResolvedValue(jsonResponse(SUITES));
    renderPage();
    expect(await screen.findByRole('heading', { name: /Eval Suites/i })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /New Suite/i })).toBeInTheDocument();
  });

  test('loads suite cards with dataset versions, the latest run and KPI totals', async () => {
    vi.spyOn(globalThis, 'fetch').mockResolvedValue(jsonResponse(SUITES));
    renderPage();
    expect(await screen.findByText('Q1 Quality Benchmarks')).toBeInTheDocument();
    expect(screen.getByText('Regression Guard')).toBeInTheDocument();
    expect(screen.getByText('12 tasks · dataset v3')).toBeInTheDocument();
    expect(screen.getByTestId('suite-run-suite-1')).toHaveTextContent('Completed · 83% pass');
    // A durable run in progress shows its per-task progress.
    expect(screen.getByTestId('suite-run-suite-2')).toHaveTextContent('Running · 2/5 done');
    expect(screen.getByTestId('suite-run-suite-3')).toHaveTextContent(/Stalled/);
    expect(screen.queryByTestId('suite-run-suite-4')).not.toBeInTheDocument();
    expect(screen.getByText('Total Suites')).toBeInTheDocument();
    expect(screen.getByTestId('kpi-running')).toHaveTextContent('1');
    expect(screen.getByTestId('kpi-attention')).toHaveTextContent('1');
  });

  test('shows the empty state when there are no suites', async () => {
    vi.spyOn(globalThis, 'fetch').mockResolvedValue(jsonResponse([]));
    renderPage();
    expect(await screen.findByText(/No eval suites yet/i)).toBeInTheDocument();
  });

  test('shows the error state when the request fails', async () => {
    vi.spyOn(globalThis, 'fetch').mockResolvedValue(jsonResponse({ detail: 'boom' }, 500));
    renderPage();
    expect(await screen.findByText(/Failed to load eval suites/i)).toBeInTheDocument();
  });

  test('running a suite POSTs to the run endpoint', async () => {
    const spy = vi.spyOn(globalThis, 'fetch').mockImplementation(async (input, init) => {
      const url = String(input);
      const method = (init?.method ?? 'GET').toUpperCase();
      if (url.includes('/run') && method === 'POST') return jsonResponse({ run_id: 'r-1' });
      return jsonResponse(SUITES);
    });
    renderPage();
    await screen.findByText('Q1 Quality Benchmarks');
    await userEvent.click(screen.getAllByRole('button', { name: /^Run$/i })[0]);
    await waitFor(() =>
      expect(
        spy.mock.calls.some(
          ([u, i]) => /\/intelligence\/eval-suites\/suite-1\/run$/.test(String(u)) && (i as RequestInit)?.method === 'POST'
        )
      ).toBe(true)
    );
  });

  test('create modal is gated then POSTs a new suite', async () => {
    const spy = vi.spyOn(globalThis, 'fetch').mockImplementation(async (input, init) => {
      const url = String(input);
      const method = (init?.method ?? 'GET').toUpperCase();
      if (url.endsWith('/intelligence/eval-suites') && method === 'POST') {
        return jsonResponse({ suite_id: 'new-suite', name: 'My Suite', task_count: 0, created_at: 'now' });
      }
      return jsonResponse(SUITES);
    });
    renderPage();
    await screen.findByText('Q1 Quality Benchmarks');
    await userEvent.click(screen.getByRole('button', { name: /New Suite/i }));

    expect(await screen.findByText('New Eval Suite')).toBeInTheDocument();
    const createBtn = screen.getByRole('button', { name: /Create Suite/i });
    expect(createBtn).toBeDisabled();

    await userEvent.type(screen.getByPlaceholderText('Q1 Quality Benchmarks'), 'My Suite');
    expect(createBtn).toBeEnabled();
    await userEvent.click(createBtn);

    await waitFor(() =>
      expect(
        spy.mock.calls.some(
          ([u, i]) => /\/intelligence\/eval-suites$/.test(String(u)) && (i as RequestInit)?.method === 'POST'
        )
      ).toBe(true)
    );
  });

  test('cancelling the create modal closes it', async () => {
    vi.spyOn(globalThis, 'fetch').mockResolvedValue(jsonResponse(SUITES));
    renderPage();
    await screen.findByText('Q1 Quality Benchmarks');
    await userEvent.click(screen.getByRole('button', { name: /New Suite/i }));
    expect(await screen.findByText('New Eval Suite')).toBeInTheDocument();
    await userEvent.click(screen.getByRole('button', { name: /^Cancel$/i }));
    await waitFor(() => expect(screen.queryByText('New Eval Suite')).not.toBeInTheDocument());
  });
});
