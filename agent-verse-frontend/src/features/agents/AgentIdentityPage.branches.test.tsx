import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter, Route, Routes } from 'react-router-dom';
import { afterEach, beforeEach, describe, expect, test, vi } from 'vitest';
import { useAuthStore } from '@/stores/auth';
import { AgentIdentityPage } from './AgentIdentityPage';

// Companion suite to AgentIdentityPage.test.tsx — targets branches not
// covered there: no-agent-selected guard, retry-on-error, issue-credential
// modal (scopes/expiry/key-type + submit), private-key display + copy,
// revoke confirm/cancel flow, JWT preview fetch (success + error), domain
// identity section, and the back-navigation button.

function json(body: unknown, status = 200) {
  return new Response(JSON.stringify(body), { status, headers: { 'Content-Type': 'application/json' } });
}

const MOCK_AGENT = {
  agent_id: 'agent-id-1',
  name: 'Identity Bot',
  autonomy_mode: 'supervised',
  created_at: '2026-01-01T00:00:00Z',
};

function renderPage(agentId = 'agent-id-1') {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <MemoryRouter initialEntries={[`/agents/${agentId}/identity`]}>
      <QueryClientProvider client={qc}>
        <Routes>
          <Route path="/agents/:agentId/identity" element={<AgentIdentityPage />} />
          <Route path="/agents/:agentId" element={<div>Agent detail page</div>} />
        </Routes>
      </QueryClientProvider>
    </MemoryRouter>
  );
}

beforeEach(() => {
  useAuthStore.setState({ apiKey: 'test-key', tenantId: 'tenant-1', plan: 'enterprise', isAuthenticated: true });
  Object.assign(navigator, { clipboard: { writeText: vi.fn().mockResolvedValue(undefined) } });
});
afterEach(() => vi.restoreAllMocks());

describe('AgentIdentityPage branches', () => {
  test('shows "No agent selected" when the route has no agentId', () => {
    const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    render(
      <MemoryRouter initialEntries={['/identity']}>
        <QueryClientProvider client={qc}>
          <Routes>
            <Route path="/identity" element={<AgentIdentityPage />} />
          </Routes>
        </QueryClientProvider>
      </MemoryRouter>
    );
    expect(screen.getByText(/no agent selected/i)).toBeInTheDocument();
  });

  test('error state Retry button calls refetch', async () => {
    let callCount = 0;
    vi.spyOn(globalThis, 'fetch').mockImplementation(async (input) => {
      const url = String(input);
      if (url.includes('/credentials')) {
        callCount += 1;
        return callCount === 1 ? json({ error: 'boom' }, 500) : json([]);
      }
      if (url.includes('/agents/')) return json(MOCK_AGENT);
      return json(null, 404);
    });
    renderPage();
    expect(await screen.findByRole('alert')).toBeInTheDocument();
    expect(screen.getByText(/failed to load credentials/i)).toBeInTheDocument();
    await userEvent.click(screen.getByText('Retry'));
    await waitFor(() => expect(screen.queryByRole('alert')).not.toBeInTheDocument());
  });

  test('back button navigates to the agent detail page', async () => {
    vi.spyOn(globalThis, 'fetch').mockImplementation(async (input) => {
      const url = String(input);
      if (url.includes('/credentials')) return json([]);
      if (url.includes('/agents/')) return json(MOCK_AGENT);
      return json(null, 404);
    });
    renderPage();
    await screen.findByRole('heading', { name: 'Agent Identity' });
    await userEvent.click(screen.getByText(/identity bot \/ identity/i));
    expect(await screen.findByText('Agent detail page')).toBeInTheDocument();
  });

  test('issue-credential modal: toggle scope, pick expiry, submit; the one-time PEM is shown', async () => {
    const spy = vi.spyOn(globalThis, 'fetch').mockImplementation(async (input, init) => {
      const url = String(input);
      const method = (init?.method ?? 'GET').toUpperCase();
      if (url.includes('/credentials') && method === 'POST') {
        // The real POST /agents/{id}/credentials 201 body.
        return json({
          key_id: 'new-cred-1', scopes: ['goals:read', 'agents:read'], expires_at: null,
          private_key_pem: '-----BEGIN PRIVATE KEY-----\nsk-super-secret-value\n-----END PRIVATE KEY-----\n',
          public_key_pem: '-----BEGIN PUBLIC KEY-----\nPUB\n-----END PUBLIC KEY-----\n',
          warning: 'Private key shown ONCE',
        }, 201);
      }
      if (url.includes('/credentials')) return json([]);
      if (url.includes('/agents/')) return json(MOCK_AGENT);
      return json(null, 404);
    });
    renderPage();
    await screen.findByRole('heading', { name: 'Agent Identity' });
    await userEvent.click(screen.getByText(/issue new credential/i));
    expect(await screen.findByRole('dialog')).toBeInTheDocument();

    // Only RS256 service accounts exist: no key-type picker.
    expect(screen.queryByText('API_KEY')).not.toBeInTheDocument();
    await userEvent.click(screen.getByText('agents:read'));
    await userEvent.click(screen.getByText('7d'));

    await userEvent.click(screen.getByRole('button', { name: 'Issue Credential' }));

    await waitFor(() =>
      expect(spy.mock.calls.some(([u, i]) => {
        if (!String(u).includes('/agents/agent-id-1/credentials') || (i as RequestInit)?.method !== 'POST') return false;
        const body = JSON.parse(String((i as RequestInit)?.body));
        return !('key_type' in body) && body.scopes.includes('agents:read') && body.expires_in_days === 7;
      })).toBe(true)
    );
    expect(await screen.findByText(/save this private key now/i)).toBeInTheDocument();
    expect(screen.getByText(/sk-super-secret-value/)).toBeInTheDocument();
    await userEvent.click(screen.getByText(/i've saved it/i));
    expect(screen.queryByText(/save this private key now/i)).not.toBeInTheDocument();
  });

  test('issue-credential modal: cancel closes without submitting', async () => {
    const spy = vi.spyOn(globalThis, 'fetch').mockImplementation(async (input) => {
      const url = String(input);
      if (url.includes('/credentials')) return json([]);
      if (url.includes('/agents/')) return json(MOCK_AGENT);
      return json(null, 404);
    });
    renderPage();
    await screen.findByRole('heading', { name: 'Agent Identity' });
    await userEvent.click(screen.getByText(/issue new credential/i));
    await screen.findByRole('dialog');
    await userEvent.click(screen.getByText('Cancel'));
    await waitFor(() => expect(screen.queryByRole('dialog')).not.toBeInTheDocument());
    expect(spy.mock.calls.some(([, i]) => (i as RequestInit)?.method === 'POST')).toBe(false);
  });

  test('deselecting every scope disables Issue Credential', async () => {
    vi.spyOn(globalThis, 'fetch').mockImplementation(async (input) => {
      const url = String(input);
      if (url.includes('/credentials')) return json([]);
      if (url.includes('/agents/')) return json(MOCK_AGENT);
      return json(null, 404);
    });
    renderPage();
    await screen.findByRole('heading', { name: 'Agent Identity' });
    await userEvent.click(screen.getByText(/issue new credential/i));
    await screen.findByRole('dialog');
    await userEvent.click(screen.getByText('goals:read'));
    expect(screen.getByRole('button', { name: 'Issue Credential' })).toBeDisabled();
  });

  test('revoke flow: confirm sends DELETE, then cancel dismisses without deleting', async () => {
    const spy = vi.spyOn(globalThis, 'fetch').mockImplementation(async (input, init) => {
      const url = String(input);
      const method = (init?.method ?? 'GET').toUpperCase();
      if (url.includes('/credentials/cred-1') && method === 'DELETE') return json(null, 204);
      if (url.includes('/credentials'))
        return json([{
          key_id: 'cred-1', key_type: 'service_account', scopes: ['goals:read'],
          expires_at: null, last_used_at: null, revoked_at: null, description: '',
        }]);
      if (url.includes('/agents/')) return json(MOCK_AGENT);
      return json(null, 404);
    });
    renderPage();
    await screen.findByText('cred-1');
    await userEvent.click(screen.getByRole('button', { name: /revoke credential cred-1/i }));
    expect(await screen.findByText(/revoke credential\?/i)).toBeInTheDocument();
    await userEvent.click(screen.getByRole('button', { name: 'Revoke' }));
    await waitFor(() =>
      expect(spy.mock.calls.some(([u, i]) =>
        String(u).includes('/credentials/cred-1') && (i as RequestInit)?.method === 'DELETE'
      )).toBe(true)
    );
  });

  test('revoke flow: cancel dismisses the confirm modal without a DELETE call', async () => {
    const spy = vi.spyOn(globalThis, 'fetch').mockImplementation(async (input) => {
      const url = String(input);
      if (url.includes('/credentials'))
        return json([{
          key_id: 'cred-2', key_type: 'service_account', scopes: ['goals:read'],
          expires_at: null, last_used_at: null, revoked_at: null, description: '',
        }]);
      if (url.includes('/agents/')) return json(MOCK_AGENT);
      return json(null, 404);
    });
    renderPage();
    await screen.findByText('cred-2');
    await userEvent.click(screen.getByRole('button', { name: /revoke credential cred-2/i }));
    await screen.findByText(/revoke credential\?/i);
    await userEvent.click(screen.getByRole('button', { name: 'Cancel' }));
    await waitFor(() => expect(screen.queryByText(/revoke credential\?/i)).not.toBeInTheDocument());
    expect(spy.mock.calls.some(([, i]) => (i as RequestInit)?.method === 'DELETE')).toBe(false);
  });

  test('revoked credential card hides the revoke button', async () => {
    vi.spyOn(globalThis, 'fetch').mockImplementation(async (input) => {
      const url = String(input);
      if (url.includes('/credentials'))
        return json([{
          key_id: 'cred-3', key_type: 'service_account', scopes: [], expires_at: null,
          last_used_at: '2026-01-01T00:00:00Z', revoked_at: '2026-01-02T00:00:00Z', description: 'old key',
        }]);
      if (url.includes('/agents/')) return json(MOCK_AGENT);
      return json(null, 404);
    });
    renderPage();
    await screen.findByText('cred-3');
    expect(screen.queryByRole('button', { name: /revoke credential cred-3/i })).not.toBeInTheDocument();
    expect(screen.getByText('revoked')).toBeInTheDocument();
    expect(screen.getByText('old key')).toBeInTheDocument();
  });

  test('copy button on a credential card writes the key id to the clipboard', async () => {
    vi.spyOn(globalThis, 'fetch').mockImplementation(async (input) => {
      const url = String(input);
      if (url.includes('/credentials'))
        return json([{
          key_id: 'cred-4', key_type: 'service_account', scopes: ['goals:read'],
          expires_at: null, last_used_at: null, revoked_at: null,
        }]);
      if (url.includes('/agents/')) return json(MOCK_AGENT);
      return json(null, 404);
    });
    renderPage();
    const card = (await screen.findByText('cred-4')).closest('div')!.parentElement as HTMLElement;
    await userEvent.click(within(card).getByLabelText('Copy key ID'));
    expect(navigator.clipboard.writeText).toHaveBeenCalledWith('cred-4');
  });

  test('token exchange help explains the client assertion and makes no /token call', async () => {
    const spy = vi.spyOn(globalThis, 'fetch').mockImplementation(async (input) => {
      const url = String(input);
      if (url.includes('/credentials')) return json([]);
      if (url.includes('/agents/')) return json(MOCK_AGENT);
      return json(null, 404);
    });
    renderPage();
    await screen.findByRole('heading', { name: 'Agent Identity' });
    expect(screen.getByRole('heading', { name: /token exchange/i })).toBeInTheDocument();
    expect(screen.getByText(/client-assertion-type:jwt-bearer/)).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: /fetch token/i })).not.toBeInTheDocument();
    expect(spy.mock.calls.some(([u]) => String(u).includes('/token'))).toBe(false);
  });

  test('domain identity: selecting a domain reveals fields; saving shows "Saved"', async () => {
    vi.spyOn(globalThis, 'fetch').mockImplementation(async (input) => {
      const url = String(input);
      if (url.includes('/credentials')) return json([]);
      if (url.includes('/agents/')) return json(MOCK_AGENT);
      return json(null, 404);
    });
    renderPage();
    await screen.findByRole('heading', { name: 'Agent Identity' });
    expect(screen.getByText(/select a domain context/i)).toBeInTheDocument();

    const select = screen.getByDisplayValue('— Select domain —');
    await userEvent.selectOptions(select, 'legal');
    expect(screen.getByText('Bar Number')).toBeInTheDocument();
    await userEvent.type(screen.getByPlaceholderText('CA-123456'), 'CA-999999');
    await userEvent.click(screen.getByText('Save Domain Identity'));
    expect(await screen.findByText('Saved')).toBeInTheDocument();
  });

  test('domain identity: switching domains resets the field values', async () => {
    vi.spyOn(globalThis, 'fetch').mockImplementation(async (input) => {
      const url = String(input);
      if (url.includes('/credentials')) return json([]);
      if (url.includes('/agents/')) return json(MOCK_AGENT);
      return json(null, 404);
    });
    renderPage();
    await screen.findByRole('heading', { name: 'Agent Identity' });
    const select = screen.getByDisplayValue('— Select domain —');
    await userEvent.selectOptions(select, 'healthcare');
    expect(screen.getByText('NPI Number')).toBeInTheDocument();
    await userEvent.selectOptions(select, 'finance');
    expect(screen.getByText('Trader ID')).toBeInTheDocument();
    expect(screen.queryByText('NPI Number')).not.toBeInTheDocument();
  });
});
