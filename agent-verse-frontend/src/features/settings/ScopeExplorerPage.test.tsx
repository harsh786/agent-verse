import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter } from 'react-router-dom';
import { afterEach, beforeEach, describe, expect, test, vi } from 'vitest';
import { useAuthStore } from '@/stores/auth';
import { useToastStore } from '@/stores/toast';
import { ScopeExplorerPage } from './ScopeExplorerPage';

// Mock clipboard API – not available in jsdom
Object.defineProperty(navigator, 'clipboard', {
  value: { writeText: vi.fn().mockResolvedValue(undefined) },
  writable: true,
  configurable: true,
});

const mockNavigate = vi.fn();
vi.mock('react-router-dom', async () => {
  const actual = await vi.importActual('react-router-dom');
  return { ...actual, useNavigate: () => mockNavigate };
});

function renderPage() {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={qc}>
      <MemoryRouter>
        <ScopeExplorerPage />
      </MemoryRouter>
    </QueryClientProvider>
  );
}

function mockFetch(
  plan = 'professional',
  opts: {
    keys?: unknown[];
    activity?: unknown;
    createError?: boolean;
    revokeError?: boolean;
  } = {}
) {
  const keys = opts.keys ?? [
    { key_id: 'k1', name: 'Production Key', scopes: ['goals:read'], created_at: '2026-01-01T00:00:00Z' },
  ];
  return vi.spyOn(globalThis, 'fetch').mockImplementation(async (input, init) => {
    const url = String(input);
    const method = (init?.method ?? 'GET').toUpperCase();

    if (url.includes('/auth/keys/activity'))
      return new Response(JSON.stringify(opts.activity ?? {}), { status: 200, headers: { 'Content-Type': 'application/json' } });

    if (url.includes('/tenants/me/keys/') && method === 'DELETE') {
      if (opts.revokeError)
        return new Response(JSON.stringify({ detail: 'Could not revoke key' }), { status: 500, headers: { 'Content-Type': 'application/json' } });
      return new Response(null, { status: 204 });
    }

    if (url.includes('/tenants/me/keys') && method === 'POST') {
      if (opts.createError)
        return new Response(JSON.stringify({ detail: 'Could not create key' }), { status: 500, headers: { 'Content-Type': 'application/json' } });
      return new Response(
        JSON.stringify({ raw_key: 'sk-raw-abcdef1234567890', key_id: 'new-k1' }),
        { status: 200, headers: { 'Content-Type': 'application/json' } }
      );
    }

    if (url.includes('/tenants/me/keys'))
      return new Response(JSON.stringify(keys), { status: 200, headers: { 'Content-Type': 'application/json' } });

    if (url.includes('/tenants/me'))
      return new Response(
        JSON.stringify({ tenant_id: 'tid-1', name: 'ACME', plan }),
        { status: 200, headers: { 'Content-Type': 'application/json' } }
      );
    return new Response('[]', { status: 200, headers: { 'Content-Type': 'application/json' } });
  });
}

describe('ScopeExplorerPage', () => {
  beforeEach(() => {
    useAuthStore.setState({
      apiKey: 'test-key', tenantId: 'tenant-1', plan: 'professional', isAuthenticated: true,
    });
    useToastStore.setState({ toasts: [] });
  });
  afterEach(() => vi.restoreAllMocks());

  test('renders without crashing', async () => {
    mockFetch();
    renderPage();
    await waitFor(() => expect(document.body).toBeTruthy());
  });

  test('renders goals scope group', async () => {
    mockFetch();
    renderPage();
    // The page shows scope groups — "goals" should appear somewhere
    await waitFor(() => expect(screen.getAllByText(/goals/i).length).toBeGreaterThanOrEqual(1));
  });

  test('renders agents scope group', async () => {
    mockFetch();
    renderPage();
    await waitFor(() => expect(screen.getAllByText(/agents/i).length).toBeGreaterThanOrEqual(1));
  });

  test('shows scopes with granted check marks for professional plan', async () => {
    mockFetch('professional');
    renderPage();
    // goals:read should be visible somewhere in scope list
    await waitFor(() => expect(screen.getAllByText(/goals:read/i).length).toBeGreaterThanOrEqual(1));
  });

  test('shows error state when tenant fetch fails', async () => {
    vi.spyOn(globalThis, 'fetch').mockResolvedValue(
      new Response('error', { status: 500 })
    );
    renderPage();
    await waitFor(() =>
      expect(screen.getByText(/failed to load/i)).toBeInTheDocument()
    );
  });

  test('search input is present', async () => {
    mockFetch();
    renderPage();
    await waitFor(() => expect(screen.getByPlaceholderText(/search/i)).toBeInTheDocument());
  });

  test('API keys section renders', async () => {
    mockFetch();
    renderPage();
    await waitFor(() => expect(screen.getByTestId('api-keys-section')).toBeInTheDocument());
  });

  test('retries fetching tenant data after an error', async () => {
    const user = userEvent.setup();
    let tenantCallCount = 0;
    vi.spyOn(globalThis, 'fetch').mockImplementation(async (input) => {
      const url = String(input);
      if (url.includes('/tenants/me/keys')) return new Response('[]', { status: 200, headers: { 'Content-Type': 'application/json' } });
      if (url.includes('/auth/keys/activity')) return new Response('{}', { status: 200, headers: { 'Content-Type': 'application/json' } });
      if (url.includes('/tenants/me')) {
        tenantCallCount += 1;
        if (tenantCallCount === 1) return new Response('error', { status: 500 });
        return new Response(
          JSON.stringify({ tenant_id: 'tid-1', name: 'ACME', plan: 'professional' }),
          { status: 200, headers: { 'Content-Type': 'application/json' } }
        );
      }
      return new Response('[]', { status: 200, headers: { 'Content-Type': 'application/json' } });
    });
    renderPage();
    await screen.findByRole('alert');
    await user.click(screen.getByRole('button', { name: /retry/i }));
    await waitFor(() => expect(screen.queryByRole('alert')).not.toBeInTheDocument());
  });

  test('toggles a scope group open and closed', async () => {
    const user = userEvent.setup();
    mockFetch();
    renderPage();
    await waitFor(() => expect(screen.getByTestId('scope-groups')).toBeInTheDocument());
    // "goals" is expanded by default
    expect(await screen.findByText('goals:write')).toBeInTheDocument();
    const goalsHeader = screen.getByRole('button', { name: /goals/i });
    await user.click(goalsHeader);
    await waitFor(() => expect(screen.queryByText('goals:write')).not.toBeInTheDocument());
    await user.click(goalsHeader);
    await waitFor(() => expect(screen.getByText('goals:write')).toBeInTheDocument());
  });

  test('filters scopes by search text', async () => {
    const user = userEvent.setup();
    mockFetch();
    renderPage();
    const search = await screen.findByPlaceholderText(/search/i);
    await user.type(search, 'goals:read');
    await waitFor(() => expect(screen.queryByText('knowledge:read')).not.toBeInTheDocument());
    expect(screen.getByText('goals:read')).toBeInTheDocument();
  });

  test('shows a "no scopes match" message for an unmatched search', async () => {
    const user = userEvent.setup();
    mockFetch();
    renderPage();
    const search = await screen.findByPlaceholderText(/search/i);
    await user.type(search, 'zzz-does-not-exist-zzz');
    await waitFor(() => expect(screen.getByText(/no scopes match/i)).toBeInTheDocument());
  });

  test('shows an upgrade button for ungranted scopes and navigates to billing on click', async () => {
    const user = userEvent.setup();
    mockFetch('free');
    renderPage();
    await waitFor(() => expect(screen.getByTestId('scope-groups')).toBeInTheDocument());
    // "governance" is fully ungranted on the free plan and collapsed by default
    const governanceHeader = screen.getByRole('button', { name: /^governance/i });
    await user.click(governanceHeader);
    const upgradeButtons = await screen.findAllByRole('button', { name: /upgrade to unlock/i });
    expect(upgradeButtons.length).toBeGreaterThan(0);
    await user.click(upgradeButtons[0]);
    expect(mockNavigate).toHaveBeenCalledWith('/settings?tab=billing', { state: { highlightPlan: 'professional' } });
    await waitFor(() =>
      expect(useToastStore.getState().toasts.some((t) => t.message === 'Choose a plan to unlock more scopes')).toBe(true)
    );
  });

  test('shows "All plan scopes" for a key with no explicit scopes', async () => {
    mockFetch('professional', {
      keys: [{ key_id: 'k2', name: 'Full Access Key', created_at: '2026-02-01T00:00:00Z' }],
    });
    renderPage();
    await waitFor(() => expect(screen.getByText(/all plan scopes/i)).toBeInTheDocument());
  });

  test('shows the empty state when there are no API keys', async () => {
    mockFetch('professional', { keys: [] });
    renderPage();
    await waitFor(() => expect(screen.getByText(/no api keys yet/i)).toBeInTheDocument());
  });

  test('shows the last API call time from activity data', async () => {
    mockFetch('professional', { activity: { last_used: new Date(Date.now() - 120_000).toISOString() } });
    renderPage();
    await waitFor(() => expect(screen.getByText(/m ago/i)).toBeInTheDocument());
  });

  test('disables the create-key submit button until a name is entered', async () => {
    const user = userEvent.setup();
    mockFetch();
    renderPage();
    await user.click(await screen.findByTestId('create-key-btn'));
    const dialog = await screen.findByRole('dialog', { name: /create api key/i });
    const submit = within(dialog).getByRole('button', { name: /create api key/i });
    expect(submit).toBeDisabled();
    await user.type(within(dialog).getByPlaceholderText(/CI pipeline key/i), 'My New Key');
    expect(submit).toBeEnabled();
  });

  test('closes the create-key modal via the close button', async () => {
    const user = userEvent.setup();
    mockFetch();
    renderPage();
    await user.click(await screen.findByTestId('create-key-btn'));
    const dialog = await screen.findByRole('dialog', { name: /create api key/i });
    await user.click(within(dialog).getByRole('button', { name: /^close$/i }));
    await waitFor(() => expect(screen.queryByRole('dialog', { name: /create api key/i })).not.toBeInTheDocument());
  });

  test('closes the create-key modal via the backdrop', async () => {
    const user = userEvent.setup();
    mockFetch();
    renderPage();
    await user.click(await screen.findByTestId('create-key-btn'));
    const dialog = await screen.findByRole('dialog', { name: /create api key/i });
    const backdrop = dialog.querySelector('[aria-hidden="true"]');
    expect(backdrop).toBeTruthy();
    await user.click(backdrop as Element);
    await waitFor(() => expect(screen.queryByRole('dialog', { name: /create api key/i })).not.toBeInTheDocument());
  });

  test('creates an API key, reveals the raw key, and copies it', async () => {
    const user = userEvent.setup();
    mockFetch('professional');
    renderPage();
    await user.click(await screen.findByTestId('create-key-btn'));
    const dialog = await screen.findByRole('dialog', { name: /create api key/i });
    await user.type(within(dialog).getByPlaceholderText(/CI pipeline key/i), 'CI Key');
    const scopeCheckbox = within(dialog).getByRole('checkbox', { name: /goals:read/i });
    await user.click(scopeCheckbox);
    expect(scopeCheckbox).toBeChecked();
    await user.click(within(dialog).getByRole('button', { name: /create api key/i }));

    await screen.findByTestId('raw-key-display');
    expect(screen.getByText(/copy this key now/i)).toBeInTheDocument();
    expect(screen.queryByText('sk-raw-abcdef1234567890')).not.toBeInTheDocument();

    await user.click(screen.getByRole('button', { name: /toggle visibility/i }));
    expect(screen.getByText('sk-raw-abcdef1234567890')).toBeInTheDocument();

    await user.click(screen.getByRole('button', { name: /copy key/i }));
    await waitFor(() =>
      expect(useToastStore.getState().toasts.some((t) => t.message === 'Copied to clipboard')).toBe(true)
    );
    await waitFor(() =>
      expect(useToastStore.getState().toasts.some((t) => t.message === 'API key created')).toBe(true)
    );

    await user.click(screen.getByRole('button', { name: /^done$/i }));
    await waitFor(() => expect(screen.queryByTestId('raw-key-display')).not.toBeInTheDocument());
  });

  test('shows an error toast when key creation fails', async () => {
    const user = userEvent.setup();
    mockFetch('professional', { createError: true });
    renderPage();
    await user.click(await screen.findByTestId('create-key-btn'));
    const dialog = await screen.findByRole('dialog', { name: /create api key/i });
    await user.type(within(dialog).getByPlaceholderText(/CI pipeline key/i), 'Bad Key');
    await user.click(within(dialog).getByRole('button', { name: /create api key/i }));
    await waitFor(() =>
      expect(useToastStore.getState().toasts.some((t) => t.message === 'Failed to create API key')).toBe(true)
    );
  });

  test('revokes an API key after confirmation', async () => {
    const user = userEvent.setup();
    mockFetch('professional');
    renderPage();
    await user.click(await screen.findByTestId('revoke-btn-k1'));
    const dialog = await screen.findByRole('dialog', { name: /revoke api key/i });
    await user.click(within(dialog).getByRole('button', { name: /^revoke$/i }));
    await waitFor(() => expect(screen.queryByRole('dialog', { name: /revoke api key/i })).not.toBeInTheDocument());
    await waitFor(() =>
      expect(useToastStore.getState().toasts.some((t) => t.message === 'API key revoked')).toBe(true)
    );
  });

  test('cancels the revoke confirmation without calling the API', async () => {
    const user = userEvent.setup();
    const fetchSpy = mockFetch('professional');
    renderPage();
    await user.click(await screen.findByTestId('revoke-btn-k1'));
    const dialog = await screen.findByRole('dialog', { name: /revoke api key/i });
    await user.click(within(dialog).getByRole('button', { name: /^cancel$/i }));
    await waitFor(() => expect(screen.queryByRole('dialog', { name: /revoke api key/i })).not.toBeInTheDocument());
    expect(fetchSpy.mock.calls.some((c) => String(c[0]).includes('/tenants/me/keys/k1'))).toBe(false);
  });

  test('shows an error toast when revoke fails', async () => {
    const user = userEvent.setup();
    mockFetch('professional', { revokeError: true });
    renderPage();
    await user.click(await screen.findByTestId('revoke-btn-k1'));
    const dialog = await screen.findByRole('dialog', { name: /revoke api key/i });
    await user.click(within(dialog).getByRole('button', { name: /^revoke$/i }));
    await waitFor(() =>
      expect(useToastStore.getState().toasts.some((t) => t.message === 'Failed to revoke key')).toBe(true)
    );
  });
});
