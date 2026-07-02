import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { render, screen, waitFor } from '@testing-library/react';
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

function mockFetch(channels: unknown[] = []) {
  return vi.spyOn(globalThis, 'fetch').mockImplementation(async (input, init) => {
    const url = String(input);
    const method = (init as RequestInit | undefined)?.method ?? 'GET';
    if (url.includes('/governance/notifications') && method === 'POST')
      return new Response(JSON.stringify({ channel_id: 'c-new', type: 'webhook', status: 'created' }), { status: 201, headers: { 'Content-Type': 'application/json' } });
    if (url.includes('/governance/notifications'))
      return new Response(JSON.stringify(channels), { status: 200, headers: { 'Content-Type': 'application/json' } });
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
});
