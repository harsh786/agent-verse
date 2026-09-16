import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter } from 'react-router-dom';
import { afterEach, beforeEach, describe, expect, test, vi } from 'vitest';
import { useAuthStore } from '@/stores/auth';
import { NotificationCenterPage } from './NotificationCenterPage';

function renderPage() {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={qc}>
      <MemoryRouter><NotificationCenterPage /></MemoryRouter>
    </QueryClientProvider>,
  );
}

function mockFetch(channels: unknown[] = [], opts?: {
  testResult?: { success: boolean; message: string } | 'reject';
  listStatus?: number;
}) {
  return vi.spyOn(globalThis, 'fetch').mockImplementation(async (input, init) => {
    const url = String(input);
    const method = (init as RequestInit | undefined)?.method ?? 'GET';
    if (url.includes('/test') && method === 'POST') {
      if (opts?.testResult === 'reject') throw new Error('network down');
      return new Response(
        JSON.stringify(opts?.testResult ?? { success: true, message: 'Delivered.' }),
        { status: 200, headers: { 'Content-Type': 'application/json' } },
      );
    }
    if (url.includes('/governance/notifications') && method === 'DELETE')
      return new Response(null, { status: 204 });
    if (url.includes('/governance/notifications') && method === 'POST')
      return new Response(JSON.stringify({ channel_id: 'c-new', type: 'webhook', status: 'created' }), { status: 201, headers: { 'Content-Type': 'application/json' } });
    if (url.includes('/governance/notifications'))
      return new Response(JSON.stringify(channels), { status: opts?.listStatus ?? 200, headers: { 'Content-Type': 'application/json' } });
    return new Response('{}', { status: 200, headers: { 'Content-Type': 'application/json' } });
  });
}

beforeEach(() => {
  sessionStorage.clear(); localStorage.clear();
  useAuthStore.setState({ apiKey: 'k', tenantId: 't', plan: 'free', isAuthenticated: true });
});
afterEach(() => vi.restoreAllMocks());

describe('NotificationCenterPage', () => {
  test('renders Notification Center heading', async () => {
    mockFetch();
    renderPage();
    expect(await screen.findByRole('heading', { name: /notification center/i })).toBeInTheDocument();
  });

  test('lists existing channels', async () => {
    mockFetch([{ channel_id: 'c1', type: 'slack', enabled: true }]);
    renderPage();
    expect(await screen.findByText(/slack/i)).toBeInTheDocument();
  });

  test('empty state shown when no channels', async () => {
    mockFetch([]);
    renderPage();
    await waitFor(() => expect(screen.getByText(/No channels/i)).toBeInTheDocument());
  });

  test('add channel button is present', async () => {
    mockFetch();
    renderPage();
    await waitFor(() => expect(screen.getByTestId('add-channel-btn')).toBeInTheDocument());
  });

  test('create channel posts to governance/notifications endpoint', async () => {
    const spy = mockFetch();
    renderPage();
    // Click add channel button to show form
    await waitFor(() => expect(screen.getByTestId('add-channel-btn')).toBeInTheDocument());
    await userEvent.click(screen.getByTestId('add-channel-btn'));
    // Channel form should appear
    await waitFor(() => expect(screen.getByTestId('channel-form')).toBeInTheDocument());
    // Fill in the required webhook URL
    const urlInput = screen.getByPlaceholderText(/https:\/\//i);
    await userEvent.type(urlInput, 'https://hooks.example.com/test');
    // Click submit inside the form
    const addButtons = screen.getAllByRole('button', { name: /add channel/i });
    const formBtn = addButtons[addButtons.length - 1];
    await userEvent.click(formBtn);
    await waitFor(() =>
      expect(spy.mock.calls.some(([u, i]) =>
        String(u).includes('/governance/notifications') && (i as RequestInit)?.method === 'POST'
      )).toBe(true)
    );
  });

  test('submitting the form with no URL does not call the create endpoint', async () => {
    const spy = mockFetch();
    renderPage();
    await waitFor(() => expect(screen.getByTestId('add-channel-btn')).toBeInTheDocument());
    await userEvent.click(screen.getByTestId('add-channel-btn'));
    await waitFor(() => expect(screen.getByTestId('channel-form')).toBeInTheDocument());
    const addButtons = screen.getAllByRole('button', { name: /add channel/i });
    await userEvent.click(addButtons[addButtons.length - 1]);
    await waitFor(() => {
      expect(spy.mock.calls.some(([u, i]) =>
        String(u).includes('/governance/notifications') && (i as RequestInit)?.method === 'POST'
      )).toBe(false);
    });
  });

  test('cancel button closes the form and clears fields', async () => {
    mockFetch();
    renderPage();
    await waitFor(() => expect(screen.getByTestId('add-channel-btn')).toBeInTheDocument());
    await userEvent.click(screen.getByTestId('add-channel-btn'));
    await waitFor(() => expect(screen.getByTestId('channel-form')).toBeInTheDocument());
    const urlInput = screen.getByPlaceholderText(/https:\/\//i);
    await userEvent.type(urlInput, 'https://hooks.example.com/test');
    await userEvent.click(screen.getByRole('button', { name: /cancel/i }));
    expect(screen.queryByTestId('channel-form')).not.toBeInTheDocument();
    // Reopen — fields should have been cleared
    await userEvent.click(screen.getByTestId('add-channel-btn'));
    await waitFor(() => expect(screen.getByTestId('channel-form')).toBeInTheDocument());
    expect(screen.getByPlaceholderText(/https:\/\//i)).toHaveValue('');
  });

  test('switching channel type to webhook shows endpoint/auth/method fields', async () => {
    mockFetch();
    renderPage();
    await waitFor(() => expect(screen.getByTestId('add-channel-btn')).toBeInTheDocument());
    await userEvent.click(screen.getByTestId('add-channel-btn'));
    await waitFor(() => expect(screen.getByTestId('channel-form')).toBeInTheDocument());
    const typeSelect = screen.getByTestId('channel-type-select');
    await userEvent.click(within(typeSelect).getByText(/webhook/i));
    expect(screen.getByText(/endpoint url/i)).toBeInTheDocument();
    expect(screen.getByText(/auth header/i)).toBeInTheDocument();
    const methodSelect = screen.getByDisplayValue('POST');
    await userEvent.selectOptions(methodSelect, 'PUT');
    expect(methodSelect).toHaveValue('PUT');
  });

  test('switching channel type to teams shows the Teams webhook field', async () => {
    mockFetch();
    renderPage();
    await waitFor(() => expect(screen.getByTestId('add-channel-btn')).toBeInTheDocument());
    await userEvent.click(screen.getByTestId('add-channel-btn'));
    await waitFor(() => expect(screen.getByTestId('channel-form')).toBeInTheDocument());
    const typeSelect = screen.getByTestId('channel-type-select');
    await userEvent.click(within(typeSelect).getByText(/teams/i));
    expect(screen.getByText(/connectors in your teams channel settings/i)).toBeInTheDocument();
  });

  test('toggling a channel updates its enabled badge', async () => {
    mockFetch([{ channel_id: 'c1', type: 'slack', enabled: true }]);
    renderPage();
    await waitFor(() => expect(screen.getByTestId('channel-item-c1')).toBeInTheDocument());
    expect(within(screen.getByTestId('channel-item-c1')).getByText(/active/i)).toBeInTheDocument();
    await userEvent.click(screen.getByLabelText(/disable channel/i));
    expect(within(screen.getByTestId('channel-item-c1')).getByText(/disabled/i)).toBeInTheDocument();
    // Toggle back on
    await userEvent.click(screen.getByLabelText(/enable channel/i));
    expect(within(screen.getByTestId('channel-item-c1')).getByText(/active/i)).toBeInTheDocument();
  });

  test('deleting a channel calls the DELETE endpoint', async () => {
    const spy = mockFetch([{ channel_id: 'c1', type: 'slack', enabled: true }]);
    renderPage();
    await waitFor(() => expect(screen.getByTestId('channel-item-c1')).toBeInTheDocument());
    await userEvent.click(screen.getByTestId('delete-btn-c1'));
    await waitFor(() => {
      expect(spy.mock.calls.some(([u, i]) =>
        String(u).includes('/governance/notifications/c1') && (i as RequestInit)?.method === 'DELETE'
      )).toBe(true);
    });
  });

  test('sending a successful test notification shows Delivered', async () => {
    mockFetch([{ channel_id: 'c1', type: 'slack', enabled: true }], { testResult: { success: true, message: 'ok' } });
    renderPage();
    await waitFor(() => expect(screen.getByTestId('test-btn-c1')).toBeInTheDocument());
    await userEvent.click(screen.getByTestId('test-btn-c1'));
    expect(await screen.findByText(/delivered/i)).toBeInTheDocument();
  });

  test('a failed test notification shows Failed', async () => {
    mockFetch([{ channel_id: 'c1', type: 'slack', enabled: true }], { testResult: { success: false, message: 'boom' } });
    renderPage();
    await waitFor(() => expect(screen.getByTestId('test-btn-c1')).toBeInTheDocument());
    await userEvent.click(screen.getByTestId('test-btn-c1'));
    const item = screen.getByTestId('channel-item-c1');
    await waitFor(() => expect(within(item).getByText(/failed/i)).toBeInTheDocument());
  });

  test('a rejected test notification request is caught and shows Failed', async () => {
    mockFetch([{ channel_id: 'c1', type: 'slack', enabled: true }], { testResult: 'reject' });
    renderPage();
    await waitFor(() => expect(screen.getByTestId('test-btn-c1')).toBeInTheDocument());
    await userEvent.click(screen.getByTestId('test-btn-c1'));
    const item = screen.getByTestId('channel-item-c1');
    await waitFor(() => expect(within(item).getByText(/failed/i)).toBeInTheDocument());
  });

  test('shows a failure message when the channel list fails to load', async () => {
    mockFetch([], { listStatus: 500 });
    renderPage();
    expect(await screen.findByText(/failed to load channels/i)).toBeInTheDocument();
  });

  test('Events Today stat sums events_today across channels', async () => {
    mockFetch([
      { channel_id: 'c1', type: 'slack', enabled: true, events_today: 3 },
      { channel_id: 'c2', type: 'webhook', enabled: true, events_today: 4 },
    ]);
    renderPage();
    await waitFor(() => expect(screen.getByTestId('channel-item-c1')).toBeInTheDocument());
    expect(screen.getByText('7')).toBeInTheDocument();
  });

  test('renders the notification events section', async () => {
    mockFetch([]);
    renderPage();
    expect(await screen.findByText(/approval required/i)).toBeInTheDocument();
    expect(screen.getByText(/goal complete/i)).toBeInTheDocument();
    expect(screen.getByText(/goal failed/i)).toBeInTheDocument();
  });
});
