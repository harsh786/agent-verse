import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, beforeEach, describe, expect, test, vi } from 'vitest';
import { useAuthStore } from '@/stores/auth';
import { A2ADirectorySetting } from './A2ADirectorySetting';

function json(body: unknown, status = 200) {
  return new Response(JSON.stringify(body), { status, headers: { 'Content-Type': 'application/json' } });
}

function renderSetting() {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={qc}>
      <A2ADirectorySetting />
    </QueryClientProvider>,
  );
}

beforeEach(() => {
  useAuthStore.setState({ apiKey: 'k', tenantId: 't', plan: 'free', isAuthenticated: true });
});
afterEach(() => vi.restoreAllMocks());

describe('A2ADirectorySetting', () => {
  test('shows the stored (default off) state', async () => {
    vi.spyOn(globalThis, 'fetch').mockImplementation(async () => json({ enabled: false }));
    renderSetting();
    const sw = screen.getByRole('switch', { name: /Public A2A agent directory/i });
    await waitFor(() => expect(sw).toBeEnabled());
    expect(sw).toHaveAttribute('aria-checked', 'false');
  });

  test('turning it on PUTs the tenant switch', async () => {
    const spy = vi.spyOn(globalThis, 'fetch').mockImplementation(async (_input, init) =>
      json({ enabled: (init?.method ?? 'GET') === 'PUT' }),
    );
    renderSetting();
    const sw = screen.getByRole('switch', { name: /Public A2A agent directory/i });
    await waitFor(() => expect(sw).toBeEnabled());
    await userEvent.click(sw);
    await waitFor(() => expect(sw).toHaveAttribute('aria-checked', 'true'));
    const put = spy.mock.calls.find(([, init]) => init?.method === 'PUT');
    expect(String(put?.[0])).toMatch(/\/tenants\/me\/a2a-directory$/);
    expect(JSON.parse(String(put?.[1]?.body))).toEqual({ enabled: true });
  });

  test('a non-admin refusal is shown and the switch stays off', async () => {
    vi.spyOn(globalThis, 'fetch').mockImplementation(async (_input, init) =>
      (init?.method ?? 'GET') === 'PUT' ? json({ detail: 'Admin role required' }, 403) : json({ enabled: false }),
    );
    renderSetting();
    const sw = screen.getByRole('switch', { name: /Public A2A agent directory/i });
    await waitFor(() => expect(sw).toBeEnabled());
    await userEvent.click(sw);
    await waitFor(() => expect(screen.getByRole('alert')).toBeInTheDocument());
    expect(sw).toHaveAttribute('aria-checked', 'false');
  });
});
