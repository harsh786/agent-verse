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

const SUITES = [
  {
    id: 'suite-1',
    name: 'Q1 Quality Benchmarks',
    description: 'Baseline quality checks.',
    task_count: 12,
    last_run_status: 'passed',
    pass_rate: 0.83,
    last_run_at: '2026-01-01T00:00:00Z',
    created_at: '2025-12-01T00:00:00Z',
  },
  {
    id: 'suite-2',
    name: 'Regression Guard',
    task_count: 5,
    last_run_status: 'failed',
    created_at: '2025-12-02T00:00:00Z',
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

  test('loads suite cards with KPI totals', async () => {
    vi.spyOn(globalThis, 'fetch').mockResolvedValue(jsonResponse(SUITES));
    renderPage();
    expect(await screen.findByText('Q1 Quality Benchmarks')).toBeInTheDocument();
    expect(screen.getByText('Regression Guard')).toBeInTheDocument();
    expect(screen.getByText('12 tasks')).toBeInTheDocument();
    expect(screen.getByText('83% pass')).toBeInTheDocument();
    // KPI row: Total Suites label is unique; "Passed"/"Failed" also appear as
    // status badges, so assert the KPI label that is unambiguous.
    expect(screen.getByText('Total Suites')).toBeInTheDocument();
    expect(screen.getByText('5 tasks')).toBeInTheDocument();
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
        return jsonResponse({ id: 'new-suite', name: 'My Suite', task_count: 0, created_at: 'now' });
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
