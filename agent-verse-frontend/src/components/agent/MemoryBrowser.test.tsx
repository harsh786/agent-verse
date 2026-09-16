import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, beforeEach, describe, expect, test, vi } from 'vitest';
import { useAuthStore } from '@/stores/auth';
import { MemoryBrowser } from './MemoryBrowser';

const ENTRIES = [
  { id: 'e1', content: 'Team ships on Fridays', source: 'execution', type: 'fact', created_at: '2026-01-01T09:00:00Z', relevance: 0.9, agent_id: 'ag1' },
  { id: 'e2', content: 'Prefers concise summaries', source: 'user', type: 'preference', created_at: '2026-01-02T09:00:00Z', agent_id: 'ag1' },
];

function mockFetch(entries: unknown[] = ENTRIES) {
  return vi.spyOn(globalThis, 'fetch').mockImplementation(async (input, init) => {
    const url = String(input);
    const method = (init?.method ?? 'GET').toUpperCase();
    if (url.includes('/memory') && method === 'DELETE')
      return new Response('{}', { status: 200, headers: { 'Content-Type': 'application/json' } });
    if (url.includes('/memory'))
      return new Response(JSON.stringify(entries), { status: 200, headers: { 'Content-Type': 'application/json' } });
    return new Response('{}', { status: 200, headers: { 'Content-Type': 'application/json' } });
  });
}

function renderBrowser(props: { agentId?: string } = {}) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={qc}>
      <MemoryBrowser agentId="ag1" {...props} />
    </QueryClientProvider>,
  );
}

beforeEach(() => {
  sessionStorage.clear(); localStorage.clear();
  useAuthStore.setState({ apiKey: 'k', tenantId: 't', plan: 'free', isAuthenticated: true });
});
afterEach(() => vi.restoreAllMocks());

describe('MemoryBrowser', () => {
  test('renders memory entries with content, type and source', async () => {
    mockFetch();
    renderBrowser();
    expect(await screen.findByText('Team ships on Fridays')).toBeInTheDocument();
    expect(screen.getByText('Prefers concise summaries')).toBeInTheDocument();
    expect(screen.getByText('fact')).toBeInTheDocument();
    expect(screen.getByText('preference')).toBeInTheDocument();
  });

  test('shows the entry count in the header', async () => {
    mockFetch();
    renderBrowser();
    await screen.findByText('Team ships on Fridays');
    expect(screen.getByText('2 entries')).toBeInTheDocument();
  });

  test('scopes the query to the agent id', async () => {
    const spy = mockFetch();
    renderBrowser({ agentId: 'agent-777' });
    await screen.findByText('Team ships on Fridays');
    expect(spy.mock.calls.some(([u]) => String(u).includes('/agents/agent-777/memory'))).toBe(true);
  });

  test('typing in search forwards the search param', async () => {
    const spy = mockFetch();
    renderBrowser();
    await screen.findByText('Team ships on Fridays');
    await userEvent.type(screen.getByPlaceholderText('Search memory…'), 'fridays');
    await waitFor(() =>
      expect(spy.mock.calls.some(([u]) => String(u).includes('search=fridays'))).toBe(true),
    );
  });

  test('expanding an entry reveals the relevance detail', async () => {
    mockFetch();
    renderBrowser();
    const row = (await screen.findByText('Team ships on Fridays')).closest('.px-4') as HTMLElement;
    expect(screen.queryByText(/relevance:/i)).not.toBeInTheDocument();
    await userEvent.click(within(row).getByRole('button', { name: /expand/i }));
    expect(await screen.findByText(/relevance:\s*90%/i)).toBeInTheDocument();
  });

  test('deleting an entry fires a DELETE for that id', async () => {
    const spy = mockFetch();
    renderBrowser();
    const row = (await screen.findByText('Team ships on Fridays')).closest('.px-4') as HTMLElement;
    await userEvent.click(within(row).getByRole('button', { name: /delete memory entry/i }));
    await waitFor(() =>
      expect(spy.mock.calls.some(([u, i]) =>
        /\/agents\/ag1\/memory\/e1$/.test(String(u)) && (i as RequestInit)?.method === 'DELETE',
      )).toBe(true),
    );
  });

  test('renders the empty state when the agent has no memory', async () => {
    mockFetch([]);
    renderBrowser();
    expect(await screen.findByText('No memory entries')).toBeInTheDocument();
  });
});
