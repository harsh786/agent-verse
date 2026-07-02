import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter } from 'react-router-dom';
import { afterEach, beforeEach, describe, expect, test, vi } from 'vitest';
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

function mockFetch(roles: unknown[] = [], ips: unknown[] = []) {
  return vi.spyOn(globalThis, 'fetch').mockImplementation(async (input, init) => {
    const url = String(input);
    const method = (init as RequestInit | undefined)?.method ?? 'GET';
    if (url.includes('/tenants/me/roles') && method === 'GET')
      return new Response(JSON.stringify(roles), { status: 200, headers: { 'Content-Type': 'application/json' } });
    if (url.includes('/tenants/me/roles') && method === 'POST')
      return new Response(JSON.stringify({ id: 'r-new', user_id: 'bob', role: 'viewer' }), { status: 201, headers: { 'Content-Type': 'application/json' } });
    if (url.includes('/tenants/me/ip-allowlist') && method === 'GET')
      return new Response(JSON.stringify(ips), { status: 200, headers: { 'Content-Type': 'application/json' } });
    if (url.includes('/tenants/me/ip-allowlist') && method === 'POST')
      return new Response(JSON.stringify({ id: 'e-new', cidr: '192.168.0.0/16', description: '' }), { status: 201, headers: { 'Content-Type': 'application/json' } });
    return new Response('{}', { status: 200, headers: { 'Content-Type': 'application/json' } });
  });
}

beforeEach(() => {
  sessionStorage.clear(); localStorage.clear();
  useAuthStore.setState({ apiKey: 'k', tenantId: 't', plan: 'free', isAuthenticated: true });
});
afterEach(() => vi.restoreAllMocks());

describe('RbacPage', () => {
  test('renders Access Control heading', async () => {
    mockFetch();
    renderPage();
    expect(await screen.findByRole('heading', { name: /access control/i })).toBeInTheDocument();
  });

  test('lists role assignments', async () => {
    mockFetch([{ id: 'r1', user_id: 'alice', role: 'approver', created_at: '' }]);
    renderPage();
    expect(await screen.findByText('alice')).toBeInTheDocument();
  });

  test('shows grant role button', async () => {
    mockFetch();
    renderPage();
    await waitFor(() => expect(screen.getByTestId('grant-role-btn')).toBeInTheDocument());
  });

  test('lists IP allowlist entries', async () => {
    mockFetch([], [{ id: 'e1', cidr: '10.0.0.0/8', description: 'office' }]);
    renderPage();
    // Click IP Allowlist tab
    await waitFor(() => expect(screen.getByTestId('tab-ip')).toBeInTheDocument());
    await userEvent.click(screen.getByTestId('tab-ip'));
    expect(await screen.findByText('10.0.0.0/8')).toBeInTheDocument();
  });

  test('add IP sends POST to ip-allowlist', async () => {
    const spy = mockFetch();
    renderPage();
    await waitFor(() => expect(screen.getByTestId('tab-ip')).toBeInTheDocument());
    await userEvent.click(screen.getByTestId('tab-ip'));
    await waitFor(() => expect(screen.getByTestId('add-cidr-btn')).toBeInTheDocument());
    // Find cidr input
    const cidrInput = screen.getByPlaceholderText(/10\.0\.0\.0|CIDR/i);
    await userEvent.type(cidrInput, '192.168.0.0/16');
    await userEvent.click(screen.getByTestId('add-cidr-btn'));
    await waitFor(() =>
      expect(spy.mock.calls.some(([u, i]) =>
        String(u).includes('/tenants/me/ip-allowlist') && (i as RequestInit)?.method === 'POST'
      )).toBe(true)
    );
  });
});
