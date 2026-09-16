import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter } from 'react-router-dom';
import { afterEach, beforeEach, describe, expect, test, vi } from 'vitest';
import { useAuthStore } from '@/stores/auth';
import { APIKeyManager } from './APIKeyManager';

// NOTE: this suite never renders or asserts a real secret. The create mock
// returns an obvious PLACEHOLDER string so the one-time reveal banner can be
// verified without printing a genuine key.
const PLACEHOLDER_KEY = 'av_placeholder_do_not_use';

const KEYS = [
  { key_id: 'key-1', name: 'Production', scopes: ['orgs:read', 'missions:write'], created_at: '2026-01-01T00:00:00Z', key_prefix: 'avk_1234' },
];

function json(body: unknown, status = 200) {
  return new Response(JSON.stringify(body), { status, headers: { 'Content-Type': 'application/json' } });
}

function mockFetch(opts: { keys?: unknown[]; pending?: boolean } = {}) {
  return vi.spyOn(globalThis, 'fetch').mockImplementation(async (input, init) => {
    const url = String(input);
    const method = (init?.method ?? 'GET').toUpperCase();
    if (url.includes('/v1/tenants/api-keys') && method === 'POST') return json({ key: PLACEHOLDER_KEY, key_id: 'key-new' });
    if (/\/v1\/tenants\/api-keys\/[^/]+/.test(url) && method === 'DELETE') return json({ ok: true });
    if (url.includes('/v1/tenants/api-keys')) {
      if (opts.pending) return new Promise<Response>(() => {});
      return json(opts.keys ?? KEYS);
    }
    return json({});
  });
}

function renderPage() {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={qc}>
      <MemoryRouter><APIKeyManager orgId="org-1" /></MemoryRouter>
    </QueryClientProvider>,
  );
}

beforeEach(() => {
  sessionStorage.clear(); localStorage.clear();
  useAuthStore.setState({ apiKey: 'k', tenantId: 't', plan: 'free', isAuthenticated: true });
});
afterEach(() => vi.restoreAllMocks());

describe('APIKeyManager', () => {
  test('shows loading skeletons while keys are loading', () => {
    mockFetch({ pending: true });
    const { container } = renderPage();
    expect(container.querySelectorAll('.animate-pulse').length).toBeGreaterThan(0);
  });

  test('renders the empty state when there are no keys', async () => {
    mockFetch({ keys: [] });
    renderPage();
    expect(await screen.findByText(/No API keys yet/i)).toBeInTheDocument();
  });

  test('lists existing keys with their name and scopes', async () => {
    mockFetch();
    renderPage();
    expect(await screen.findByText('Production')).toBeInTheDocument();
    expect(screen.getByText('orgs:read')).toBeInTheDocument();
    expect(screen.getByText('missions:write')).toBeInTheDocument();
  });

  test('create flow POSTs to the api-keys endpoint and reveals the one-time key banner', async () => {
    const spy = mockFetch({ keys: [] });
    renderPage();
    await screen.findByText(/No API keys yet/i);
    await userEvent.click(screen.getByRole('button', { name: 'Create key' }));
    await userEvent.type(await screen.findByPlaceholderText(/Production key/i), 'CI key');
    await userEvent.click(screen.getByRole('button', { name: 'Create Key' }));
    await waitFor(() => {
      const post = spy.mock.calls.find(([u, i]) =>
        String(u).includes('/v1/tenants/api-keys') && (i as RequestInit)?.method === 'POST');
      expect(post).toBeTruthy();
      const body = JSON.parse(String((post?.[1] as RequestInit)?.body ?? '{}'));
      expect(body.name).toBe('CI key');
      expect(body.org_id).toBe('org-1');
      expect(body.scopes).toContain('orgs:read');
    });
    expect(await screen.findByText(/Key created — copy it now/i)).toBeInTheDocument();
    expect(screen.getByText(PLACEHOLDER_KEY)).toBeInTheDocument();
  });

  test('toggling a scope includes it in the create payload', async () => {
    const spy = mockFetch({ keys: [] });
    renderPage();
    await screen.findByText(/No API keys yet/i);
    await userEvent.click(screen.getByRole('button', { name: 'Create key' }));
    await userEvent.type(await screen.findByPlaceholderText(/Production key/i), 'Admin key');
    await userEvent.click(screen.getByRole('button', { name: 'admin' }));
    await userEvent.click(screen.getByRole('button', { name: 'Create Key' }));
    await waitFor(() => {
      const post = spy.mock.calls.find(([u, i]) =>
        String(u).includes('/v1/tenants/api-keys') && (i as RequestInit)?.method === 'POST');
      const body = JSON.parse(String((post?.[1] as RequestInit)?.body ?? '{}'));
      expect(body.scopes).toContain('admin');
    });
  });

  test('deleting a key DELETEs the api-keys endpoint', async () => {
    const spy = mockFetch();
    renderPage();
    await screen.findByText('Production');
    await userEvent.click(screen.getByRole('button', { name: /Delete key Production/i }));
    await waitFor(() =>
      expect(spy.mock.calls.some(([u, i]) =>
        String(u).includes('/v1/tenants/api-keys/key-1') && (i as RequestInit)?.method === 'DELETE',
      )).toBe(true),
    );
  });
});
