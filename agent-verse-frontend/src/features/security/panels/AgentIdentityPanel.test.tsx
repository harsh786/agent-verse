import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, beforeEach, describe, expect, test, vi } from 'vitest';
import { useAuthStore } from '@/stores/auth';
import { AgentIdentityPanel } from './AgentIdentityPanel';

const AGENTS = { agents: [{ id: 'a1', name: 'CEO Agent' }, { id: 'a2', name: 'CTO Agent' }] };

const KEYS = {
  keys: [
    { key_id: 'k1', name: 'jira-only-key', allowed_tools: ['jira_create', 'jira_update'],
      denied_tools: [], created_at: 1_700_000_000, last_used_at: null, is_active: true, use_count: 4 },
    { key_id: 'k2', name: 'revoked-key', allowed_tools: null,
      denied_tools: [], created_at: 1_700_000_000, last_used_at: 1_700_100_000, is_active: false, use_count: 0 },
  ],
};

interface MockOpts { keys?: unknown }

function mockFetch(opts: MockOpts = {}) {
  const { keys = KEYS } = opts;
  return vi.spyOn(globalThis, 'fetch').mockImplementation(async (input, init) => {
    const url = String(input);
    const method = ((init as RequestInit | undefined)?.method ?? 'GET').toUpperCase();
    const json = (body: unknown) =>
      new Response(JSON.stringify(body), { status: 200, headers: { 'Content-Type': 'application/json' } });

    if (url.includes('/keys') && method === 'DELETE') return json({ status: 'revoked' });
    if (url.includes('/keys') && method === 'POST') return json({ key_id: 'k3', name: 'new-key' });
    if (url.includes('/keys')) return json(keys);
    if (url.includes('/agents')) return json(AGENTS);
    return json({});
  });
}

function renderPanel() {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={qc}><AgentIdentityPanel /></QueryClientProvider>,
  );
}

beforeEach(() => {
  sessionStorage.clear(); localStorage.clear();
  useAuthStore.setState({ apiKey: 'k', tenantId: 't', plan: 'free', isAuthenticated: true });
});
afterEach(() => vi.restoreAllMocks());

describe('AgentIdentityPanel', () => {
  test('renders concept explainer and key-management sections', () => {
    mockFetch();
    renderPanel();
    expect(screen.getByText(/What is Agent Identity\?/i)).toBeInTheDocument();
    expect(screen.getByRole('heading', { name: /Per-Agent API Keys/i })).toBeInTheDocument();
    expect(screen.getByRole('heading', { name: /Delegation Lineage/i })).toBeInTheDocument();
  });

  test('populates the agent selector from the agents API', async () => {
    mockFetch();
    renderPanel();
    expect(await screen.findByRole('option', { name: 'CEO Agent' })).toBeInTheDocument();
    expect(screen.getByRole('option', { name: 'CTO Agent' })).toBeInTheDocument();
  });

  test('selecting an agent lists its keys with status and usage', async () => {
    mockFetch();
    renderPanel();
    await screen.findByRole('option', { name: 'CEO Agent' });
    await userEvent.selectOptions(screen.getByRole('combobox'), 'a1');
    expect(await screen.findByText('jira-only-key')).toBeInTheDocument();
    expect(screen.getByText('Active')).toBeInTheDocument();
    expect(screen.getByText('Revoked')).toBeInTheDocument();
    expect(screen.getByText(/Used 4x/i)).toBeInTheDocument();
    // Allowed-tool chips render for the key that has an allowlist.
    expect(screen.getByText('jira_create')).toBeInTheDocument();
  });

  test('shows the no-keys hint when an agent has no keys', async () => {
    mockFetch({ keys: { keys: [] } });
    renderPanel();
    await screen.findByRole('option', { name: 'CEO Agent' });
    await userEvent.selectOptions(screen.getByRole('combobox'), 'a1');
    expect(await screen.findByText(/No agent keys/i)).toBeInTheDocument();
  });

  test('creating a key POSTs to /agents/:id/keys', async () => {
    const spy = mockFetch({ keys: { keys: [] } });
    renderPanel();
    await screen.findByRole('option', { name: 'CEO Agent' });
    await userEvent.selectOptions(screen.getByRole('combobox'), 'a1');
    await userEvent.click(screen.getByRole('button', { name: /Create Key/i }));
    await userEvent.type(screen.getByPlaceholderText(/jira-only-key/i), 'my-new-key');
    await userEvent.click(screen.getByRole('button', { name: /^Create Key$/i }));
    await waitFor(() =>
      expect(spy.mock.calls.some(([u, i]) =>
        String(u).includes('/agents/a1/keys') && (i as RequestInit)?.method === 'POST'),
      ).toBe(true),
    );
  });

  test('revoking an active key DELETEs /agents/:id/keys/:keyId', async () => {
    const spy = mockFetch();
    renderPanel();
    await screen.findByRole('option', { name: 'CEO Agent' });
    await userEvent.selectOptions(screen.getByRole('combobox'), 'a1');
    await userEvent.click(await screen.findByRole('button', { name: /Revoke/i }));
    await waitFor(() =>
      expect(spy.mock.calls.some(([u, i]) =>
        String(u).includes('/agents/a1/keys/k1') && (i as RequestInit)?.method === 'DELETE'),
      ).toBe(true),
    );
  });
});
