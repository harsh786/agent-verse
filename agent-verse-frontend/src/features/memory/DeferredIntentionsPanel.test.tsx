import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, beforeEach, describe, expect, test, vi } from 'vitest';
import { useAuthStore } from '@/stores/auth';
import { DeferredIntentionsPanel } from './DeferredIntentionsPanel';

function renderPanel() {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={qc}>
      <DeferredIntentionsPanel />
    </QueryClientProvider>,
  );
}

const ITEM = {
  id: 'i1',
  intention: 'Check the nightly deploy',
  due_at: '2099-01-01T09:00:00Z',
  expires_at: '2099-02-01T09:00:00Z',
  state: 'pending',
  source_goal_id: '',
  agent_id: null,
  result: null,
};

beforeEach(() => {
  useAuthStore.setState({ apiKey: 'k', tenantId: 't', plan: 'free', isAuthenticated: true });
});
afterEach(() => vi.restoreAllMocks());

describe('DeferredIntentionsPanel (MEM-16)', () => {
  test('lists pending intentions and schedules a new one', async () => {
    let items = [ITEM];
    const calls: Array<{ url: string; method: string; body?: string }> = [];
    vi.spyOn(globalThis, 'fetch').mockImplementation(async (input, init) => {
      const url = String(input);
      const method = (init?.method ?? 'GET').toUpperCase();
      calls.push({ url, method, body: init?.body as string | undefined });
      if (url.includes('/memory/prospective') && method === 'POST') {
        const created = { ...ITEM, id: 'i2', intention: 'Rotate keys' };
        items = [...items, created];
        return new Response(JSON.stringify(created), { status: 201 });
      }
      return new Response(JSON.stringify(items), { status: 200 });
    });
    renderPanel();
    expect(await screen.findByText('Check the nightly deploy')).toBeInTheDocument();

    await userEvent.type(screen.getByPlaceholderText(/check the nightly deploy status/i), 'Rotate keys');
    await userEvent.click(screen.getByRole('button', { name: 'Schedule' }));

    expect(await screen.findByText('Rotate keys')).toBeInTheDocument();
    const post = calls.find((c) => c.method === 'POST');
    expect(post && JSON.parse(post.body ?? '{}').intention).toBe('Rotate keys');
  });

  test('cancels an intention', async () => {
    let items = [ITEM];
    vi.spyOn(globalThis, 'fetch').mockImplementation(async (input, init) => {
      const method = (init?.method ?? 'GET').toUpperCase();
      if (String(input).includes('/memory/prospective/i1') && method === 'DELETE') {
        items = [];
        return new Response(null, { status: 204 });
      }
      return new Response(JSON.stringify(items), { status: 200 });
    });
    renderPanel();
    await userEvent.click(await screen.findByRole('button', { name: /cancel intention/i }));
    await waitFor(() => expect(screen.getByText(/no pending intentions/i)).toBeInTheDocument());
  });

  test('MEM-43: requests failed intentions and shows the failed state with attempts', async () => {
    const urls: string[] = [];
    vi.spyOn(globalThis, 'fetch').mockImplementation(async (input) => {
      urls.push(String(input));
      return new Response(
        JSON.stringify([
          {
            ...ITEM,
            id: 'f1',
            intention: 'Rotate the keys',
            state: 'failed',
            attempts: 5,
            result: { error: 'RuntimeError: daily goal limit reached' },
          },
        ]),
        { status: 200 },
      );
    });
    renderPanel();
    expect(await screen.findByText('Rotate the keys')).toBeInTheDocument();
    expect(urls.some((u) => u.includes('include_failed=true'))).toBe(true);
    expect(screen.getByText(/failed after 5 attempts/i)).toBeInTheDocument();
    expect(screen.getByText(/daily goal limit reached/i)).toBeInTheDocument();
  });

  test('shows an error state when the list fails', async () => {
    vi.spyOn(globalThis, 'fetch').mockResolvedValue(new Response('{"detail":"down"}', { status: 503 }));
    renderPanel();
    expect(await screen.findByText(/could not load deferred intentions/i)).toBeInTheDocument();
  });
});
