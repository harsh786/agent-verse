import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter } from 'react-router-dom';
import { beforeEach, expect, test, vi } from 'vitest';
import { useAuthStore } from '@/stores/auth';
import { RbacPage } from './RbacPage';

function renderPage() {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={qc}>
      <MemoryRouter><RbacPage /></MemoryRouter>
    </QueryClientProvider>,
  );
}

beforeEach(() => {
  sessionStorage.clear(); localStorage.clear();
  useAuthStore.setState({ apiKey: 'k', tenantId: 't', plan: 'free', isAuthenticated: true });
});

test('lists roles and ip-allowlist entries', async () => {
  vi.spyOn(globalThis, 'fetch').mockImplementation(async (input) => {
    const url = String(input);
    if (url.includes('/tenants/me/roles'))
      return new Response(JSON.stringify([{ id: 'r1', user_id: 'alice', role: 'approver' }]),
        { status: 200, headers: { 'Content-Type': 'application/json' } });
    if (url.includes('/tenants/me/ip-allowlist'))
      return new Response(JSON.stringify([{ id: 'e1', cidr: '10.0.0.0/8', description: 'office' }]),
        { status: 200, headers: { 'Content-Type': 'application/json' } });
    return new Response('[]', { status: 200, headers: { 'Content-Type': 'application/json' } });
  });
  renderPage();
  expect(await screen.findByText('alice')).toBeInTheDocument();
  expect(await screen.findByText('10.0.0.0/8')).toBeInTheDocument();
});

test('add IP entry posts cidr', async () => {
  const f = vi.spyOn(globalThis, 'fetch').mockImplementation(async (input, init) => {
    const url = String(input);
    if (url.includes('/tenants/me/ip-allowlist') && (init as RequestInit)?.method === 'POST')
      return new Response(JSON.stringify({ id: 'e2', cidr: '192.168.0.0/16', description: '' }), { status: 201 });
    return new Response('[]', { status: 200, headers: { 'Content-Type': 'application/json' } });
  });
  renderPage();
  await screen.findByLabelText(/cidr/i);
  await userEvent.type(screen.getByLabelText(/cidr/i), '192.168.0.0/16');
  await userEvent.click(screen.getByRole('button', { name: /add cidr/i }));
  await waitFor(() =>
    expect(f.mock.calls.some(([u, i]) =>
      String(u).includes('/tenants/me/ip-allowlist') && (i as RequestInit)?.method === 'POST')).toBe(true),
  );
});
