import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter, Route, Routes } from 'react-router-dom';
import { afterEach, beforeEach, describe, expect, test, vi } from 'vitest';
import { useAuthStore } from '@/stores/auth';
import { useToastStore } from '@/stores/toast';
import { AgentCredentialsPage } from './AgentCredentialsPage';

const FUTURE = '2999-01-01T00:00:00Z';
const PAST = '2000-01-01T00:00:00Z';

const CREDS = [
  { credential_id: 'c1', key_prefix: 'av_live_ab', description: 'CI pipeline', scopes: ['read:goals', 'write:goals'], issued_at: '2026-01-01T00:00:00Z', expires_at: FUTURE },
  { credential_id: 'c2', key_prefix: 'av_live_cd', description: 'Old integration', scopes: ['read:goals'], issued_at: '2025-01-01T00:00:00Z', expires_at: PAST },
];

const CRED_NO_EXPIRY = [
  { credential_id: 'c3', key_prefix: 'av_live_ef', description: 'No expiry key', scopes: ['read:goals'], issued_at: '2026-01-01T00:00:00Z', last_used_at: '2026-02-01T00:00:00Z' },
];

function mockFetch(creds: unknown[] = CREDS, opts: { credError?: boolean } = {}) {
  return vi.spyOn(globalThis, 'fetch').mockImplementation(async (input, init) => {
    const url = String(input);
    const method = (init?.method ?? 'GET').toUpperCase();
    if (url.includes('/credentials') && method === 'POST')
      return new Response(JSON.stringify({ credential_id: 'new', api_key: 'av_live_SECRETKEY123' }),
        { status: 200, headers: { 'Content-Type': 'application/json' } });
    if (url.match(/\/credentials\/.+/) && method === 'DELETE')
      return new Response('{}', { status: 200, headers: { 'Content-Type': 'application/json' } });
    if (url.includes('/credentials')) {
      if (opts.credError) return new Response('nope', { status: 500, headers: { 'Content-Type': 'text/plain' } });
      return new Response(JSON.stringify(creds), { status: 200, headers: { 'Content-Type': 'application/json' } });
    }
    return new Response('{}', { status: 200, headers: { 'Content-Type': 'application/json' } });
  });
}

function renderPage(agentId = 'agent-1') {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={qc}>
      <MemoryRouter initialEntries={[`/agents/${agentId}/credentials`]}>
        <Routes>
          <Route path="/agents/:agentId/credentials" element={<AgentCredentialsPage />} />
        </Routes>
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

beforeEach(() => {
  sessionStorage.clear(); localStorage.clear();
  useAuthStore.setState({ apiKey: 'k', tenantId: 't', plan: 'free', isAuthenticated: true });
});
afterEach(() => vi.restoreAllMocks());

describe('AgentCredentialsPage', () => {
  test('renders the heading and the agent id from the route', async () => {
    mockFetch();
    renderPage('agent-42');
    expect(await screen.findByRole('heading', { name: /Agent Credentials/i })).toBeInTheDocument();
    expect(screen.getByText('agent-42')).toBeInTheDocument();
  });

  test('renders credential rows with prefix, description and scopes', async () => {
    mockFetch();
    renderPage();
    expect(await screen.findByText('CI pipeline')).toBeInTheDocument();
    expect(screen.getByText('Old integration')).toBeInTheDocument();
    expect(screen.getByText('av_live_ab…')).toBeInTheDocument();
    expect(screen.getByText('write:goals')).toBeInTheDocument();
  });

  test('KPI row counts active and expired keys', async () => {
    mockFetch();
    renderPage();
    await screen.findByText('CI pipeline');
    // One future-dated (active) + one past-dated (expired) → Total 2, Active 1, Expired 1.
    const total = screen.getByText('Total Keys').closest('div')!;
    const active = screen.getByText('Active').closest('div')!;
    const expired = screen.getByText('Expired', { selector: 'p' }).closest('div')!;
    expect(within(total).getByText('2')).toBeInTheDocument();
    expect(within(active).getByText('1')).toBeInTheDocument();
    expect(within(expired).getByText('1')).toBeInTheDocument();
    // Expired credential shows the Expired badge.
    expect(screen.getByText('Expired', { selector: 'span' })).toBeInTheDocument();
  });

  test('issuing a key POSTs the body and reveals the one-time key banner', async () => {
    const spy = mockFetch();
    renderPage('agent-9');
    await screen.findByText('CI pipeline');

    await userEvent.click(screen.getByRole('button', { name: /issue key/i }));
    const dialog = screen.getByRole('dialog');
    await userEvent.type(within(dialog).getByPlaceholderText(/CI pipeline integration/i), 'New robot key');
    await userEvent.click(within(dialog).getByRole('button', { name: /issue key/i }));

    // The freshly issued api_key is shown once.
    expect(await screen.findByText('av_live_SECRETKEY123')).toBeInTheDocument();
    await waitFor(() =>
      expect(spy.mock.calls.some(([u, i]) =>
        String(u).includes('/agents/agent-9/credentials')
        && (i as RequestInit)?.method === 'POST'
        && String((i as RequestInit)?.body).includes('New robot key'),
      )).toBe(true),
    );
  });

  test('revoking a credential fires a DELETE for that id', async () => {
    const spy = mockFetch();
    renderPage('agent-3');
    await screen.findByText('CI pipeline');
    const row = screen.getByText('CI pipeline').closest('.rounded-xl') as HTMLElement;
    await userEvent.click(within(row).getByRole('button', { name: /revoke credential/i }));
    await waitFor(() =>
      expect(spy.mock.calls.some(([u, i]) =>
        /\/agents\/agent-3\/credentials\/c1$/.test(String(u)) && (i as RequestInit)?.method === 'DELETE',
      )).toBe(true),
    );
  });

  test('shows the empty state when no credentials exist', async () => {
    mockFetch([]);
    renderPage();
    expect(await screen.findByText('No credentials issued')).toBeInTheDocument();
  });

  test('shows an error alert when the credential request fails', async () => {
    mockFetch([], { credError: true });
    renderPage();
    expect(await screen.findByText('Failed to load credentials.')).toBeInTheDocument();
  });

  test('copying the freshly issued key writes to the clipboard and toasts success', async () => {
    Object.assign(navigator, { clipboard: { writeText: vi.fn().mockResolvedValue(undefined) } });
    mockFetch();
    renderPage('agent-9');
    await screen.findByText('CI pipeline');

    await userEvent.click(screen.getByRole('button', { name: /issue key/i }));
    const dialog = screen.getByRole('dialog');
    await userEvent.type(within(dialog).getByPlaceholderText(/CI pipeline integration/i), 'New robot key');
    await userEvent.click(within(dialog).getByRole('button', { name: /issue key/i }));

    const banner = await screen.findByText('av_live_SECRETKEY123');
    await userEvent.click(screen.getByTitle('Copy'));

    expect(navigator.clipboard.writeText).toHaveBeenCalledWith('av_live_SECRETKEY123');
    expect(useToastStore.getState().toasts.some((t) => t.kind === 'success' && t.message === 'API key copied')).toBe(true);
    expect(banner).toBeInTheDocument();
  });

  test('dismissing the new-key banner hides it', async () => {
    mockFetch();
    renderPage('agent-9');
    await screen.findByText('CI pipeline');

    await userEvent.click(screen.getByRole('button', { name: /issue key/i }));
    const dialog = screen.getByRole('dialog');
    await userEvent.type(within(dialog).getByPlaceholderText(/CI pipeline integration/i), 'New robot key');
    await userEvent.click(within(dialog).getByRole('button', { name: /issue key/i }));

    await screen.findByText('av_live_SECRETKEY123');
    await userEvent.click(screen.getByText('✕'));
    expect(screen.queryByText('av_live_SECRETKEY123')).not.toBeInTheDocument();
  });

  test('cancel button closes the issue modal without submitting', async () => {
    const spy = mockFetch();
    renderPage('agent-9');
    await screen.findByText('CI pipeline');

    await userEvent.click(screen.getByRole('button', { name: /issue key/i }));
    const dialog = screen.getByRole('dialog');
    await userEvent.click(within(dialog).getByRole('button', { name: /cancel/i }));

    expect(screen.queryByRole('dialog')).not.toBeInTheDocument();
    expect(spy.mock.calls.some(([, i]) => (i as RequestInit)?.method === 'POST')).toBe(false);
  });

  test('clicking the modal backdrop closes it', async () => {
    mockFetch();
    renderPage('agent-9');
    await screen.findByText('CI pipeline');

    await userEvent.click(screen.getByRole('button', { name: /issue key/i }));
    const dialog = screen.getByRole('dialog');
    const backdrop = dialog.querySelector('.absolute.inset-0') as HTMLElement;
    await userEvent.click(backdrop);

    expect(screen.queryByRole('dialog')).not.toBeInTheDocument();
  });

  test('issuing a key with custom scopes and blank expiry omits expires_in_days', async () => {
    const spy = mockFetch();
    renderPage('agent-9');
    await screen.findByText('CI pipeline');

    await userEvent.click(screen.getByRole('button', { name: /issue key/i }));
    const dialog = screen.getByRole('dialog');
    await userEvent.type(within(dialog).getByPlaceholderText(/CI pipeline integration/i), 'Custom scoped key');

    const scopesInput = within(dialog).getByPlaceholderText('read:goals write:goals');
    await userEvent.clear(scopesInput);
    await userEvent.type(scopesInput, 'read:agents');

    const expiryInput = within(dialog).getByPlaceholderText('90');
    await userEvent.clear(expiryInput);

    await userEvent.click(within(dialog).getByRole('button', { name: /issue key/i }));

    await screen.findByText('av_live_SECRETKEY123');
    await waitFor(() =>
      expect(spy.mock.calls.some(([u, i]) => {
        if (!String(u).includes('/agents/agent-9/credentials') || (i as RequestInit)?.method !== 'POST') return false;
        const body = JSON.parse(String((i as RequestInit)?.body));
        return body.scopes.length === 1 && body.scopes[0] === 'read:agents' && body.expires_in_days === undefined;
      })).toBe(true),
    );
  });

  test('the Issue Key submit button stays disabled while the description is blank or whitespace', async () => {
    mockFetch();
    renderPage('agent-9');
    await screen.findByText('CI pipeline');

    await userEvent.click(screen.getByRole('button', { name: /issue key/i }));
    const dialog = screen.getByRole('dialog');
    const submit = within(dialog).getByRole('button', { name: /issue key/i });
    expect(submit).toBeDisabled();

    await userEvent.type(within(dialog).getByPlaceholderText(/CI pipeline integration/i), '   ');
    expect(submit).toBeDisabled();
  });

  test('a credential without an expiry or last-used date renders without the Expires/Expired markers, and one with last_used_at shows it', async () => {
    mockFetch(CRED_NO_EXPIRY);
    renderPage();
    await screen.findByText('No expiry key');
    const row = screen.getByText('No expiry key').closest('.rounded-xl') as HTMLElement;

    expect(within(row).queryByText(/Expired/)).not.toBeInTheDocument();
    expect(within(row).queryByText(/Expires /)).not.toBeInTheDocument();
    expect(within(row).getByText(/Last used/)).toBeInTheDocument();
  });
});
