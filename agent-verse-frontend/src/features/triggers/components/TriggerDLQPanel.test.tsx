import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, beforeEach, describe, expect, test, vi } from 'vitest';
import { useAuthStore } from '@/stores/auth';
import { TriggerDLQPanel } from './TriggerDLQPanel';
import type { TriggerDLQEntry } from '../types';

const ENTRY: TriggerDLQEntry = {
  id: 'dlq-1',
  trigger_id: 'sched-abcdef123456789',
  failure_type: 'dispatch_error',
  error_message: 'Connector timed out after 30s',
  retry_count: 2,
  created_at: '2026-01-01T10:00:00Z',
  next_retry_at: '2026-01-01T11:00:00Z',
};

function mockDLQ(entries: TriggerDLQEntry[] = []) {
  return vi.spyOn(globalThis, 'fetch').mockImplementation(async (input, init) => {
    const url = String(input);
    const method = (init?.method ?? 'GET').toUpperCase();
    if (url.includes('/triggers/dlq/') && url.endsWith('/retry') && method === 'POST')
      return new Response('{}', { status: 200, headers: { 'Content-Type': 'application/json' } });
    if (url.includes('/triggers/dlq'))
      return new Response(JSON.stringify(entries), { status: 200, headers: { 'Content-Type': 'application/json' } });
    return new Response('{}', { status: 200, headers: { 'Content-Type': 'application/json' } });
  });
}

function renderPanel() {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={qc}>
      <TriggerDLQPanel />
    </QueryClientProvider>,
  );
}

beforeEach(() => {
  sessionStorage.clear();
  localStorage.clear();
  useAuthStore.setState({ apiKey: 'k', tenantId: 't', plan: 'free', isAuthenticated: true });
});
afterEach(() => vi.restoreAllMocks());

describe('TriggerDLQPanel', () => {
  test('renders the empty state when the queue has no entries', async () => {
    mockDLQ([]);
    renderPanel();
    expect(await screen.findByText(/Dead Letter Queue is empty/i)).toBeInTheDocument();
  });

  test('renders a failed entry with its failure type, error, and retry count', async () => {
    mockDLQ([ENTRY]);
    renderPanel();
    expect(await screen.findByText('dispatch_error')).toBeInTheDocument();
    expect(screen.getByText('Connector timed out after 30s')).toBeInTheDocument();
    expect(screen.getByText(/retry 2×/)).toBeInTheDocument();
    // Trigger id is truncated to the first 12 chars.
    expect(screen.getByText(/sched-abcdef…/)).toBeInTheDocument();
  });

  test('Retry now POSTs to the DLQ retry endpoint for that entry', async () => {
    const spy = mockDLQ([ENTRY]);
    renderPanel();
    await screen.findByText('dispatch_error');
    await userEvent.click(screen.getByRole('button', { name: /Retry DLQ entry dlq-1/i }));
    await waitFor(() =>
      expect(
        spy.mock.calls.some(
          ([u, i]) =>
            String(u).includes('/triggers/dlq/dlq-1/retry') && (i as RequestInit)?.method === 'POST',
        ),
      ).toBe(true),
    );
  });

  test('Refresh re-fetches the dead letter queue', async () => {
    const spy = mockDLQ([]);
    renderPanel();
    await screen.findByText(/Dead Letter Queue is empty/i);
    const initialGets = spy.mock.calls.filter(
      ([u, i]) => String(u).includes('/triggers/dlq') && (i?.method ?? 'GET') === 'GET',
    ).length;
    await userEvent.click(screen.getByRole('button', { name: /Refresh dead letter queue/i }));
    await waitFor(() =>
      expect(
        spy.mock.calls.filter(
          ([u, i]) => String(u).includes('/triggers/dlq') && (i?.method ?? 'GET') === 'GET',
        ).length,
      ).toBeGreaterThan(initialGets),
    );
  });
});
