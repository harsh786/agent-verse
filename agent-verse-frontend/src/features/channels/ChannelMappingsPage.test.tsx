import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { render, screen, waitFor, within } from '@testing-library/react';
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

  test('shows loading skeletons before the mappings resolve', async () => {
    mockFetch();
    const { container } = renderPage();
    expect(container.querySelectorAll('.animate-pulse').length).toBe(2);
    await waitFor(() => expect(container.querySelectorAll('.animate-pulse').length).toBe(0));
  });

  test('shows a fallback icon for an unmapped channel type', async () => {
    mockFetch({ mappings: [{ id: 'm9', channel_type: 'webhook', channel_id: 'wh-1' }] });
    renderPage();
    expect(await screen.findByText('wh-1')).toBeInTheDocument();
    expect(screen.getByLabelText('webhook')).toHaveTextContent('🔗');
  });

  test('clicking Retry re-triggers the mappings fetch', async () => {
    const spy = mockFetch({ listError: true });
    renderPage();
    const alert = await screen.findByRole('alert');
    const callsBefore = spy.mock.calls.length;
    await userEvent.click(within(alert).getByRole('button', { name: /Retry/i }));
    await waitFor(() => expect(spy.mock.calls.length).toBeGreaterThan(callsBefore));
  });

  test('clicking the backdrop closes the add channel modal', async () => {
    mockFetch({ mappings: [] });
    const { container } = renderPage();
    await userEvent.click(screen.getByRole('button', { name: /Add Channel/i }));
    await screen.findByRole('dialog', { name: /Add channel connection/i });
    const backdrop = container.querySelector('.absolute.inset-0.bg-black\\/40');
    expect(backdrop).not.toBeNull();
    await userEvent.click(backdrop as HTMLElement);
    expect(screen.queryByRole('dialog', { name: /Add channel connection/i })).not.toBeInTheDocument();
  });

  test('clicking Cancel closes the add channel modal', async () => {
    mockFetch({ mappings: [] });
    renderPage();
    await userEvent.click(screen.getByRole('button', { name: /Add Channel/i }));
    await screen.findByRole('dialog', { name: /Add channel connection/i });
    await userEvent.click(screen.getByRole('button', { name: /Cancel/i }));
    expect(screen.queryByRole('dialog', { name: /Add channel connection/i })).not.toBeInTheDocument();
  });

  test('changing channel type to a non-slack/email type updates label and placeholder', async () => {
    mockFetch({ mappings: [] });
    renderPage();
    await userEvent.click(screen.getByRole('button', { name: /Add Channel/i }));
    const dialog = await screen.findByRole('dialog', { name: /Add channel connection/i });
    const select = within(dialog).getByRole('combobox');

    await userEvent.selectOptions(select, 'email');
    expect(within(dialog).getByText('Email Address')).toBeInTheDocument();
    expect(within(dialog).getByPlaceholderText('support@company.com')).toBeInTheDocument();

    await userEvent.selectOptions(select, 'teams');
    expect(within(dialog).getByText('Channel ID')).toBeInTheDocument();
    expect(within(dialog).getByPlaceholderText('channel-id')).toBeInTheDocument();
  });

  test('shows a connecting state while the create mutation is pending', async () => {
    let resolvePost: (value: Response) => void = () => {};
    const postPromise = new Promise<Response>((resolve) => {
      resolvePost = resolve;
    });
    vi.spyOn(globalThis, 'fetch').mockImplementation(async (input, init) => {
      const url = String(input);
      const method = ((init as RequestInit | undefined)?.method ?? 'GET').toUpperCase();
      if (url.includes('/channels/mappings') && method === 'POST') return postPromise;
      if (url.includes('/channels/mappings')) {
        return new Response(JSON.stringify([]), { status: 200, headers: { 'Content-Type': 'application/json' } });
      }
      return new Response(JSON.stringify({}), { status: 200, headers: { 'Content-Type': 'application/json' } });
    });

    renderPage();
    await userEvent.click(screen.getByRole('button', { name: /Add Channel/i }));
    await userEvent.type(screen.getByPlaceholderText('T12345ABCD'), 'T99999ZZZZ');
    await userEvent.click(screen.getByRole('button', { name: /Connect Channel/i }));
    expect(await screen.findByRole('button', { name: /Connecting/i })).toBeInTheDocument();

    resolvePost(new Response(JSON.stringify({ id: 'm3' }), { status: 200, headers: { 'Content-Type': 'application/json' } }));
    await waitFor(() => expect(screen.queryByRole('dialog', { name: /Add channel connection/i })).not.toBeInTheDocument());
  });
});
