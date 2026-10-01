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

// The real GET /agents/{id}/credentials row shape (AgentIdentityService.list_credentials).
function row(over: Record<string, unknown>) {
  return {
    id: 'uuid', agent_id: 'agent-1', key_id: 'kid_x', key_type: 'service_account',
    scopes: ['goals:read'], expires_at: null, revoked_at: null, last_used_at: null,
    created_by: 'key-1', created_at: '2026-01-01T00:00:00Z', description: '', ...over,
  };
}

const CREDS = [
  row({ key_id: 'kid_ci', description: 'CI pipeline', scopes: ['goals:read', 'goals:write'], expires_at: FUTURE }),
  row({ key_id: 'kid_old', description: 'Old integration', expires_at: PAST, created_at: '2025-01-01T00:00:00Z' }),
  row({ key_id: 'kid_rev', description: 'Revoked one', revoked_at: '2026-02-01T00:00:00Z' }),
];

const CRED_NO_EXPIRY = [
  row({ key_id: 'kid_ne', description: 'No expiry key', last_used_at: '2026-02-01T00:00:00Z' }),
];

// The real POST /agents/{id}/credentials 201 body.
const ISSUED = {
  key_id: 'kid_new',
  private_key_pem: '-----BEGIN PRIVATE KEY-----\nSECRETPEM\n-----END PRIVATE KEY-----\n',
  public_key_pem: '-----BEGIN PUBLIC KEY-----\nPUB\n-----END PUBLIC KEY-----\n',
  scopes: ['goals:read'],
  expires_at: FUTURE,
  warning: 'Private key shown ONCE — save it immediately and securely.',
};

function mockFetch(creds: unknown[] = CREDS, opts: { credError?: boolean } = {}) {
  return vi.spyOn(globalThis, 'fetch').mockImplementation(async (input, init) => {
    const url = String(input);
    const method = (init?.method ?? 'GET').toUpperCase();
    if (url.includes('/credentials') && method === 'POST')
      return new Response(JSON.stringify(ISSUED), { status: 201, headers: { 'Content-Type': 'application/json' } });
    if (url.match(/\/credentials\/.+/) && method === 'DELETE')
      return new Response(null, { status: 204 });
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

async function issueOne(description = 'New robot key') {
  await userEvent.click(screen.getByRole('button', { name: /issue key/i }));
  const dialog = screen.getByRole('dialog');
  await userEvent.type(within(dialog).getByPlaceholderText(/CI pipeline integration/i), description);
  await userEvent.click(within(dialog).getByRole('button', { name: /issue key/i }));
  return dialog;
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

  test('renders credential rows with key id, description and scopes', async () => {
    mockFetch();
    renderPage();
    expect(await screen.findByText('CI pipeline')).toBeInTheDocument();
    expect(screen.getByText('Old integration')).toBeInTheDocument();
    expect(screen.getByText('kid_ci')).toBeInTheDocument();
    expect(screen.getByText('goals:write')).toBeInTheDocument();
    expect(screen.queryByText(/undefined/)).not.toBeInTheDocument();
  });

  test('KPI row counts active, expired and revoked keys from revoked_at/expires_at', async () => {
    mockFetch();
    renderPage();
    await screen.findByText('CI pipeline');
    const total = screen.getByText('Total Keys').closest('div')!;
    const active = screen.getByText('Active').closest('div')!;
    const inactive = screen.getByText('Expired / Revoked').closest('div')!;
    expect(within(total).getByText('3')).toBeInTheDocument();
    expect(within(active).getByText('1')).toBeInTheDocument();
    expect(within(inactive).getByText('2')).toBeInTheDocument();
    expect(screen.getByText('Expired', { selector: 'span' })).toBeInTheDocument();
    expect(screen.getByText('Revoked', { selector: 'span' })).toBeInTheDocument();
  });

  test('issuing a key POSTs agent scopes and reveals the one-time private key', async () => {
    const spy = mockFetch();
    renderPage('agent-9');
    await screen.findByText('CI pipeline');
    await issueOne();

    expect(await screen.findByText(/SECRETPEM/)).toBeInTheDocument();
    expect(screen.getByText('kid_new')).toBeInTheDocument();
    await waitFor(() =>
      expect(spy.mock.calls.some(([u, i]) => {
        if (!String(u).includes('/agents/agent-9/credentials') || (i as RequestInit)?.method !== 'POST') return false;
        const body = JSON.parse(String((i as RequestInit)?.body));
        return body.description === 'New robot key'
          && body.scopes.join(' ') === 'goals:read goals:write'
          && !('key_type' in body);
      })).toBe(true),
    );
  });

  test('revoking a credential fires a DELETE for its key_id', async () => {
    const spy = mockFetch();
    renderPage('agent-3');
    await screen.findByText('CI pipeline');
    const card = screen.getByText('CI pipeline').closest('.rounded-xl') as HTMLElement;
    await userEvent.click(within(card).getByRole('button', { name: /revoke credential/i }));
    await waitFor(() =>
      expect(spy.mock.calls.some(([u, i]) =>
        /\/agents\/agent-3\/credentials\/kid_ci$/.test(String(u)) && (i as RequestInit)?.method === 'DELETE',
      )).toBe(true),
    );
  });

  test('a revoked credential offers no revoke button', async () => {
    mockFetch();
    renderPage();
    await screen.findByText('Revoked one');
    const card = screen.getByText('Revoked one').closest('.rounded-xl') as HTMLElement;
    expect(within(card).queryByRole('button', { name: /revoke credential/i })).not.toBeInTheDocument();
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

  test('copying the private key writes the PEM to the clipboard and toasts success', async () => {
    Object.assign(navigator, { clipboard: { writeText: vi.fn().mockResolvedValue(undefined) } });
    mockFetch();
    renderPage('agent-9');
    await screen.findByText('CI pipeline');
    await issueOne();

    await screen.findByText(/SECRETPEM/);
    await userEvent.click(screen.getByTitle('Copy'));
    expect(navigator.clipboard.writeText).toHaveBeenCalledWith(ISSUED.private_key_pem);
    expect(useToastStore.getState().toasts.some((t) => t.kind === 'success' && t.message === 'Private key copied')).toBe(true);
  });

  test('downloading the private key saves <key_id>.pem', async () => {
    const createUrl = vi.fn().mockReturnValue('blob:x');
    const revokeUrl = vi.fn();
    Object.assign(URL, { createObjectURL: createUrl, revokeObjectURL: revokeUrl });
    const click = vi.spyOn(HTMLAnchorElement.prototype, 'click').mockImplementation(() => {});
    mockFetch();
    renderPage('agent-9');
    await screen.findByText('CI pipeline');
    await issueOne();

    await screen.findByText(/SECRETPEM/);
    await userEvent.click(screen.getByTitle('Download .pem'));
    expect(createUrl).toHaveBeenCalledTimes(1);
    expect(click).toHaveBeenCalledTimes(1);
    expect((click.mock.instances[0] as unknown as HTMLAnchorElement).download).toBe('kid_new.pem');
  });

  test('dismissing the new-key banner hides it', async () => {
    mockFetch();
    renderPage('agent-9');
    await screen.findByText('CI pipeline');
    await issueOne();

    await screen.findByText(/SECRETPEM/);
    await userEvent.click(screen.getByText('✕'));
    expect(screen.queryByText(/SECRETPEM/)).not.toBeInTheDocument();
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

    const scopesInput = within(dialog).getByPlaceholderText('goals:read goals:write');
    await userEvent.clear(scopesInput);
    await userEvent.type(scopesInput, 'agents:read');

    const expiryInput = within(dialog).getByPlaceholderText('90');
    await userEvent.clear(expiryInput);

    await userEvent.click(within(dialog).getByRole('button', { name: /issue key/i }));

    await screen.findByText(/SECRETPEM/);
    await waitFor(() =>
      expect(spy.mock.calls.some(([u, i]) => {
        if (!String(u).includes('/agents/agent-9/credentials') || (i as RequestInit)?.method !== 'POST') return false;
        const body = JSON.parse(String((i as RequestInit)?.body));
        return body.scopes.length === 1 && body.scopes[0] === 'agents:read' && body.expires_in_days === undefined;
      })).toBe(true),
    );
  });

  test('the submit button stays disabled for a blank description or a scope an agent cannot hold', async () => {
    mockFetch();
    renderPage('agent-9');
    await screen.findByText('CI pipeline');

    await userEvent.click(screen.getByRole('button', { name: /issue key/i }));
    const dialog = screen.getByRole('dialog');
    const submit = within(dialog).getByRole('button', { name: /issue key/i });
    expect(submit).toBeDisabled();

    await userEvent.type(within(dialog).getByPlaceholderText(/CI pipeline integration/i), '   ');
    expect(submit).toBeDisabled();

    await userEvent.type(within(dialog).getByPlaceholderText(/CI pipeline integration/i), 'ok');
    expect(submit).toBeEnabled();
    const scopesInput = within(dialog).getByPlaceholderText('goals:read goals:write');
    await userEvent.clear(scopesInput);
    await userEvent.type(scopesInput, 'read:goals');
    expect(submit).toBeDisabled();
    expect(within(dialog).getByText(/not grantable/i)).toBeInTheDocument();
  });

  test('a credential without an expiry renders no Expires/Expired markers, and last_used_at shows', async () => {
    mockFetch(CRED_NO_EXPIRY);
    renderPage();
    await screen.findByText('No expiry key');
    const card = screen.getByText('No expiry key').closest('.rounded-xl') as HTMLElement;

    expect(within(card).queryByText(/Expired/)).not.toBeInTheDocument();
    expect(within(card).queryByText(/Expires /)).not.toBeInTheDocument();
    expect(within(card).getByText(/Last used/)).toBeInTheDocument();
  });
});
