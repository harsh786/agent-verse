/** KB-15: the ingestion DLQ is visible and each entry can be retried. */
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, beforeEach, describe, expect, test, vi } from 'vitest';
import { useAuthStore } from '@/stores/auth';
import { useToastStore } from '@/stores/toast';
import { DLQPanel } from './DLQPanel';

const json = (data: unknown, status = 200) =>
  new Response(JSON.stringify(data), { status, headers: { 'Content-Type': 'application/json' } });

const ENTRY = {
  id: 'dlq-1', source_id: 'src-1', doc_id: 'doc-9', failed_stage: 'embed', failure_type: 'pipeline_failure',
  error_message: 'embedding provider timed out', retry_count: 5, next_retry_at: null,
  created_at: '2026-09-01T00:00:00Z',
};

function renderPanel() {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } });
  render(
    <QueryClientProvider client={qc}>
      <DLQPanel />
    </QueryClientProvider>,
  );
}

function mockFetch(entries: unknown[], retryStatus = 202) {
  return vi.spyOn(globalThis, 'fetch').mockImplementation(async (input, init) => {
    const url = String(input);
    if (url.includes('/ingestion/dlq/dlq-1/retry') && init?.method === 'POST')
      return json(retryStatus === 202 ? { status: 'queued', dlq_id: 'dlq-1' } : { detail: 'DLQ entry is already resolved' }, retryStatus);
    if (url.includes('/ingestion/dlq')) return json(entries);
    return json({});
  });
}

beforeEach(() => {
  localStorage.clear();
  useAuthStore.setState({ apiKey: 'k', tenantId: 't', plan: 'free', isAuthenticated: true });
  useToastStore.setState({ toasts: [] });
});
afterEach(() => vi.restoreAllMocks());

describe('DLQPanel', () => {
  test('lists failed documents and retries one', async () => {
    const spy = mockFetch([ENTRY]);
    renderPanel();
    expect(await screen.findByText('doc-9')).toBeInTheDocument();
    expect(screen.getByText(/embedding provider timed out/)).toBeInTheDocument();
    await userEvent.click(screen.getByRole('button', { name: /Retry doc-9/i }));
    await waitFor(() =>
      expect(spy.mock.calls.some(([u, i]) => String(u).endsWith('/ingestion/dlq/dlq-1/retry') && (i as RequestInit)?.method === 'POST')).toBe(true),
    );
    await waitFor(() =>
      expect(useToastStore.getState().toasts.some((t) => /retry queued/i.test(t.message))).toBe(true),
    );
  });

  test('explains a retry the server refused', async () => {
    mockFetch([ENTRY], 409);
    renderPanel();
    await userEvent.click(await screen.findByRole('button', { name: /Retry doc-9/i }));
    await waitFor(() =>
      expect(useToastStore.getState().toasts.some((t) => /already resolved/i.test(t.message))).toBe(true),
    );
  });

  test('shows an empty state with no failures', async () => {
    mockFetch([]);
    renderPanel();
    expect(await screen.findByText(/No failed documents/i)).toBeInTheDocument();
  });
});
