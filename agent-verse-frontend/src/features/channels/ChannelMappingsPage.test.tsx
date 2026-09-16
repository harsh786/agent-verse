import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter } from 'react-router-dom';
import { afterEach, beforeEach, describe, expect, test, vi } from 'vitest';
import { useAuthStore } from '@/stores/auth';
import { ChannelMappingsPage } from './ChannelMappingsPage';

const MAPPINGS = [
  { id: 'm1', channel_type: 'slack', channel_id: 'T12345ABCD', created_at: '2026-01-01T00:00:00Z' },
  { id: 'm2', channel_type: 'email', channel_id: 'support@company.com' },
];

interface MockOpts { mappings?: unknown[]; listError?: boolean }

function mockFetch(opts: MockOpts = {}) {
  const { mappings = MAPPINGS, listError = false } = opts;
  return vi.spyOn(globalThis, 'fetch').mockImplementation(async (input, init) => {
    const url = String(input);
    const method = ((init as RequestInit | undefined)?.method ?? 'GET').toUpperCase();
    const json = (body: unknown) =>
      new Response(JSON.stringify(body), { status: 200, headers: { 'Content-Type': 'application/json' } });

    if (url.includes('/channels/mappings') && method === 'POST') return json({ id: 'm3' });
    if (url.includes('/channels/mappings')) {
      if (listError) return new Response('boom', { status: 500 });
      return json(mappings);
    }
    return json({});
  });
}

function renderPage() {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={qc}>
      <MemoryRouter><ChannelMappingsPage /></MemoryRouter>
    </QueryClientProvider>,
  );
}

beforeEach(() => {
  sessionStorage.clear(); localStorage.clear();
  useAuthStore.setState({ apiKey: 'k', tenantId: 't', plan: 'free', isAuthenticated: true });
});
afterEach(() => vi.restoreAllMocks());

describe('ChannelMappingsPage', () => {
  test('renders the heading and Add Channel action', async () => {
    mockFetch();
    renderPage();
    expect(screen.getByRole('heading', { name: /Channel Connections/i })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /Add Channel/i })).toBeInTheDocument();
  });

  test('lists channel mappings from the API', async () => {
    mockFetch();
    renderPage();
    expect(await screen.findByText('T12345ABCD')).toBeInTheDocument();
    expect(screen.getByText('support@company.com')).toBeInTheDocument();
    expect(screen.getAllByText(/Connected/i).length).toBeGreaterThanOrEqual(1);
  });

  test('shows the empty state when there are no mappings', async () => {
    mockFetch({ mappings: [] });
    renderPage();
    expect(await screen.findByText(/No channels connected/i)).toBeInTheDocument();
  });

  test('shows the error state and Retry when the load fails', async () => {
    mockFetch({ listError: true });
    renderPage();
    expect(await screen.findByRole('alert')).toHaveTextContent(/Failed to load channel mappings/i);
    expect(screen.getByRole('button', { name: /Retry/i })).toBeInTheDocument();
  });

  test('opening the modal and connecting a channel POSTs to /channels/mappings', async () => {
    const spy = mockFetch({ mappings: [] });
    renderPage();
    await userEvent.click(screen.getByRole('button', { name: /Add Channel/i }));
    expect(await screen.findByRole('dialog', { name: /Add channel connection/i })).toBeInTheDocument();
    await userEvent.type(screen.getByPlaceholderText('T12345ABCD'), 'T99999ZZZZ');
    await userEvent.click(screen.getByRole('button', { name: /Connect Channel/i }));
    await waitFor(() =>
      expect(spy.mock.calls.some(([u, i]) => {
        const init = i as RequestInit | undefined;
        return String(u).includes('/channels/mappings') &&
          init?.method === 'POST' &&
          String(init?.body ?? '').includes('T99999ZZZZ');
      })).toBe(true),
    );
  });

  test('Connect Channel is disabled until an id is entered', async () => {
    mockFetch({ mappings: [] });
    renderPage();
    await userEvent.click(screen.getByRole('button', { name: /Add Channel/i }));
    expect(await screen.findByRole('button', { name: /Connect Channel/i })).toBeDisabled();
  });
});
