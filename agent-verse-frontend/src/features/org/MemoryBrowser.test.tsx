import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter } from 'react-router-dom';
import { afterEach, beforeEach, describe, expect, test, vi } from 'vitest';
import { useAuthStore } from '@/stores/auth';
import { MemoryBrowser } from './MemoryBrowser';

const MEMORY = [
  { id: 'mem1', content: 'User prefers dark mode', scope: 'org', scope_id: 'o1', memory_type: 'preference', confidence: 0.9, tags: ['ui'] },
  { id: 'mem2', content: 'The API rate limit is 100 rpm', scope: 'org', scope_id: 'o1', memory_type: 'semantic', confidence: 0.7 },
];

function mockFetch(memory: unknown = MEMORY) {
  return vi.spyOn(globalThis, 'fetch').mockImplementation(async (input) => {
    const url = String(input);
    if (url.includes('/memory'))
      return new Response(JSON.stringify(memory), { status: 200, headers: { 'Content-Type': 'application/json' } });
    return new Response('{}', { status: 200, headers: { 'Content-Type': 'application/json' } });
  });
}

function renderBrowser() {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={qc}>
      <MemoryRouter><MemoryBrowser orgId="o1" /></MemoryRouter>
    </QueryClientProvider>,
  );
}

beforeEach(() => {
  sessionStorage.clear(); localStorage.clear();
  useAuthStore.setState({ apiKey: 'k', tenantId: 't', plan: 'free', isAuthenticated: true });
});
afterEach(() => vi.restoreAllMocks());

describe('MemoryBrowser', () => {
  test('renders memory items and the total count', async () => {
    mockFetch();
    renderBrowser();
    expect(screen.getByRole('heading', { name: /Memory Browser/i })).toBeInTheDocument();
    expect(await screen.findByText('User prefers dark mode')).toBeInTheDocument();
    expect(screen.getByText('The API rate limit is 100 rpm')).toBeInTheDocument();
    expect(screen.getByText('2 items')).toBeInTheDocument();
  });

  test('typing in the search box filters memory items client-side', async () => {
    mockFetch();
    renderBrowser();
    await screen.findByText('User prefers dark mode');
    await userEvent.type(screen.getByLabelText('Search memory content'), 'dark');
    expect(await screen.findByText('User prefers dark mode')).toBeInTheDocument();
    expect(screen.queryByText('The API rate limit is 100 rpm')).not.toBeInTheDocument();
    expect(screen.getByText('1 item matching "dark"')).toBeInTheDocument();
  });

  test('shows an empty state when the scope has no memory', async () => {
    mockFetch([]);
    renderBrowser();
    expect(await screen.findByText('No memory items in this scope.')).toBeInTheDocument();
  });

  test('the refresh button re-requests the memory endpoint', async () => {
    const spy = mockFetch();
    renderBrowser();
    await screen.findByText('User prefers dark mode');
    const before = spy.mock.calls.filter(([u]) => String(u).includes('/memory')).length;
    await userEvent.click(screen.getByRole('button', { name: /Refresh memory/i }));
    await waitFor(() =>
      expect(spy.mock.calls.filter(([u]) => String(u).includes('/memory')).length).toBeGreaterThan(before),
    );
  });
});
