import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, beforeEach, describe, expect, test, vi } from 'vitest';
import { useAuthStore } from '@/stores/auth';
import { DiscoveryPanel } from './DiscoveryPanel';

const ITEMS = [
  { id: 'i1', name: 'Triage Bot', description: 'Routes incoming tickets', category: 'agents', tags: ['support', 'routing'], popularity: 42 },
  { id: 'i2', name: 'Slack Connector', description: 'Post to Slack channels', category: 'connectors', tags: ['messaging'] },
  { id: 'i3', name: 'Invoice Template', description: '3-way match template', category: 'templates', tags: ['finance'] },
];

function mockFetch(items: unknown[] = ITEMS) {
  return vi.spyOn(globalThis, 'fetch').mockImplementation(async (input) => {
    const url = String(input);
    if (url.includes('/v1/marketplace/search'))
      return new Response(JSON.stringify(items), { status: 200, headers: { 'Content-Type': 'application/json' } });
    return new Response('{}', { status: 200, headers: { 'Content-Type': 'application/json' } });
  });
}

function renderPanel(props = {}) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={qc}>
      <DiscoveryPanel {...props} />
    </QueryClientProvider>,
  );
}

beforeEach(() => {
  sessionStorage.clear(); localStorage.clear();
  useAuthStore.setState({ apiKey: 'k', tenantId: 't', plan: 'free', isAuthenticated: true });
});
afterEach(() => vi.restoreAllMocks());

describe('DiscoveryPanel', () => {
  test('renders discovery results with name and description', async () => {
    mockFetch();
    renderPanel();
    expect(await screen.findByText('Triage Bot')).toBeInTheDocument();
    expect(screen.getByText('Routes incoming tickets')).toBeInTheDocument();
    expect(screen.getByText('Slack Connector')).toBeInTheDocument();
  });

  test('selecting a result invokes onSelect with the item', async () => {
    mockFetch();
    const onSelect = vi.fn();
    renderPanel({ onSelect });
    await screen.findByText('Triage Bot');
    await userEvent.click(screen.getByRole('button', { name: /select triage bot/i }));
    expect(onSelect).toHaveBeenCalledTimes(1);
    expect(onSelect.mock.calls[0][0]).toMatchObject({ id: 'i1', name: 'Triage Bot' });
  });

  test('switching category tab issues a request scoped to that category', async () => {
    const spy = mockFetch();
    renderPanel();
    await screen.findByText('Triage Bot');
    await userEvent.click(screen.getByRole('tab', { name: /agents/i }));
    await waitFor(() =>
      expect(spy.mock.calls.some(([u]) => String(u).includes('category=agents'))).toBe(true),
    );
  });

  test('typing in the search box forwards the query to the API', async () => {
    const spy = mockFetch();
    renderPanel();
    await screen.findByText('Triage Bot');
    await userEvent.type(screen.getByLabelText('Discovery search'), 'slack');
    await waitFor(() =>
      expect(spy.mock.calls.some(([u]) => String(u).includes('q=slack'))).toBe(true),
    );
  });

  test('clicking a tag chip filters results to that tag', async () => {
    mockFetch();
    renderPanel();
    await screen.findByText('Triage Bot');
    // 'finance' tag belongs only to the Invoice Template item.
    await userEvent.click(screen.getByRole('button', { name: 'finance' }));
    expect(screen.getByText('Invoice Template')).toBeInTheDocument();
    expect(screen.queryByText('Triage Bot')).not.toBeInTheDocument();
    expect(screen.queryByText('Slack Connector')).not.toBeInTheDocument();
  });

  test('renders the empty state when no items are returned', async () => {
    mockFetch([]);
    renderPanel();
    expect(await screen.findByText('No results found')).toBeInTheDocument();
  });
});
