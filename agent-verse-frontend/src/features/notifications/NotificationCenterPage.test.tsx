import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter } from 'react-router-dom';
import { beforeEach, expect, test, vi } from 'vitest';
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

beforeEach(() => {
  sessionStorage.clear(); localStorage.clear();
  useAuthStore.setState({ apiKey: 'k', tenantId: 't', plan: 'free', isAuthenticated: true });
});

test('lists existing channels', async () => {
  vi.spyOn(globalThis, 'fetch').mockResolvedValue(
    new Response(JSON.stringify([{ channel_id: 'c1', type: 'slack', enabled: true }]),
      { status: 200, headers: { 'Content-Type': 'application/json' } }),
  );
  renderPage();
  expect(await screen.findByText(/slack/i)).toBeInTheDocument();
});

test('create channel posts channel_type', async () => {
  const f = vi.spyOn(globalThis, 'fetch').mockImplementation(async (input, init) => {
    const url = String(input);
    if (url.includes('/governance/notifications') && (init as RequestInit)?.method === 'POST')
      return new Response(JSON.stringify({ channel_id: 'c2', type: 'webhook', status: 'created' }), { status: 201 });
    return new Response(JSON.stringify([]), { status: 200, headers: { 'Content-Type': 'application/json' } });
  });
  renderPage();
  await screen.findByRole('button', { name: /add channel/i });
  await userEvent.click(screen.getByRole('button', { name: /add channel/i }));
  await waitFor(() =>
    expect(f.mock.calls.some(([u, i]) =>
      String(u).includes('/governance/notifications') && (i as RequestInit)?.method === 'POST')).toBe(true),
  );
});
