/**
 * KB-25: the collection card offers "Re-embed" (admin) and shows the job's
 * progress from GET /knowledge/collections/{id}/re-embed until it finishes.
 */
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter } from 'react-router-dom';
import { afterEach, beforeEach, describe, expect, test, vi } from 'vitest';
import { useAuthStore } from '@/stores/auth';
import { useToastStore } from '@/stores/toast';
import { KnowledgePage } from './KnowledgePage';

const COLLECTION = { collection_id: 'col-1', name: 'Engineering Docs', doc_count: 42, embedder: 'voyage' };

function json(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), { status, headers: { 'Content-Type': 'application/json' } });
}

function renderPage() {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={qc}>
      <MemoryRouter>
        <KnowledgePage />
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

function mockFetch(opts: { post: () => Response; progress: unknown[] }) {
  const progress = [...opts.progress];
  const calls: Array<{ url: string; method: string }> = [];
  vi.spyOn(globalThis, 'fetch').mockImplementation(async (input, init) => {
    const url = String(input);
    const method = init?.method ?? 'GET';
    calls.push({ url, method });
    if (url.includes('/knowledge/collections/col-1/re-embed') && method === 'POST') return opts.post();
    if (url.includes('/knowledge/collections/col-1/re-embed'))
      return json(progress.length > 1 ? progress.shift() : progress[0]);
    if (url.includes('/knowledge/collections/col-1/stats')) return json({ chunk_count: 120 });
    if (url.includes('/knowledge/collections')) return json([COLLECTION]);
    return json({});
  });
  return calls;
}

beforeEach(() => {
  localStorage.clear();
  useAuthStore.setState({ apiKey: 'tenant-key', tenantId: 'tenant-1', plan: 'free', isAuthenticated: true });
  useToastStore.setState({ toasts: [] });
  vi.spyOn(window, 'confirm').mockReturnValue(true);
});
afterEach(() => vi.restoreAllMocks());

describe('KnowledgePage – re-embed a collection', () => {
  test('queues a re-embed and shows progress until it completes', async () => {
    const calls = mockFetch({
      post: () => json({ status: 'queued', job_id: 'job-1', collection_id: 'col-1' }, 202),
      progress: [
        { status: 'running', job_id: 'job-1', processed: 50, total: 200 },
        { status: 'completed', job_id: 'job-1', processed: 200, total: 200, dimension: 1024 },
      ],
    });
    renderPage();
    await userEvent.click(await screen.findByTestId('reembed-collection-col-1'));
    expect(calls.some((c) => c.method === 'POST' && c.url.includes('/col-1/re-embed'))).toBe(true);
    expect(await screen.findByText(/Re-embedding… 50\/200 chunks/)).toBeInTheDocument();
    await waitFor(
      () => expect(screen.getByTestId('reembed-status-col-1')).toHaveTextContent(/Re-embedded 200 chunks \(1024-d\)/),
      { timeout: 5000 },
    );
  });

  test('a 409 explains a run is in progress and shows its progress', async () => {
    mockFetch({ post: () => json({ detail: { message: 'already running' } }, 409), progress: [{ status: 'running', processed: 1, total: 2 }] });
    renderPage();
    await userEvent.click(await screen.findByTestId('reembed-collection-col-1'));
    await waitFor(() =>
      expect(useToastStore.getState().toasts.some((t) => /already running/i.test(t.message))).toBe(true),
    );
    expect(await screen.findByText(/Re-embedding… 1\/2 chunks/)).toBeInTheDocument();
    expect(screen.getByTestId('reembed-collection-col-1')).toBeDisabled();
  });

  test('a 403 explains that only admins can re-embed', async () => {
    mockFetch({ post: () => json({ detail: 'Insufficient permissions' }, 403), progress: [{ status: 'never_run' }] });
    renderPage();
    await userEvent.click(await screen.findByTestId('reembed-collection-col-1'));
    await waitFor(() =>
      expect(useToastStore.getState().toasts.some((t) => /only admins/i.test(t.message))).toBe(true),
    );
  });

  test('a failed job shows its error', async () => {
    mockFetch({
      post: () => json({ status: 'queued', job_id: 'job-2' }, 202),
      progress: [{ status: 'failed', job_id: 'job-2', error: 'no embedding provider' }],
    });
    renderPage();
    await userEvent.click(await screen.findByTestId('reembed-collection-col-1'));
    expect(await screen.findByText(/Re-embed failed: no embedding provider/)).toBeInTheDocument();
  });

  test('KB-49: asks for confirmation with the estimated cost, and a cancel queues nothing', async () => {
    const confirm = vi.spyOn(window, 'confirm').mockReturnValue(false);
    const calls = mockFetch({ post: () => json({ status: 'queued', job_id: 'j' }, 202), progress: [{ status: 'never_run' }] });
    renderPage();
    await userEvent.click(await screen.findByTestId('reembed-collection-col-1'));
    await waitFor(() => expect(confirm).toHaveBeenCalled());
    expect(String(confirm.mock.calls[0][0])).toMatch(/120 chunks — estimated embedding cost \$0\.0003/);
    expect(calls.some((c) => c.method === 'POST' && c.url.includes('/re-embed'))).toBe(false);
  });
});
