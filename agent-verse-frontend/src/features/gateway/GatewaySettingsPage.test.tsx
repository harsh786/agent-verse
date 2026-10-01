import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter, Route, Routes } from 'react-router-dom';
import { afterEach, beforeEach, describe, expect, test, vi } from 'vitest';
import { useAuthStore } from '@/stores/auth';
import { GatewaySettingsPage } from './GatewaySettingsPage';

const CONFIG = {
  max_commands_per_hour: 250,
  require_2fa_for: ['approve', 'delete'],
  channels: [
    { id: 'rest-api', name: 'REST API', alwaysOn: true, status: 'connected', description: 'Always enabled', endpoint: 'POST /v1/org/{org_id}/command' },
    { id: 'telegram', name: 'Telegram', status: 'disconnected', description: 'Bot commands' },
    { id: 'slack', name: 'Slack', status: 'connected', description: 'Slash commands' },
  ],
};

function mockGateway(config: unknown = CONFIG) {
  return vi.spyOn(globalThis, 'fetch').mockImplementation(async (input, init) => {
    const url = String(input);
    const method = (init?.method ?? 'GET').toUpperCase();
    if (/\/v1\/gateway\/[^/]+\/config$/.test(url) && method === 'GET')
      return new Response(JSON.stringify(config), { status: 200, headers: { 'Content-Type': 'application/json' } });
    return new Response(JSON.stringify({ ok: true }), { status: 200, headers: { 'Content-Type': 'application/json' } });
  });
}

function renderPage(orgId?: string) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={qc}>
      <MemoryRouter><GatewaySettingsPage orgId={orgId} /></MemoryRouter>
    </QueryClientProvider>,
  );
}

beforeEach(() => {
  sessionStorage.clear(); localStorage.clear();
  useAuthStore.setState({ apiKey: 'k', tenantId: 't', plan: 'free', isAuthenticated: true });
});
afterEach(() => vi.restoreAllMocks());

describe('GatewaySettingsPage', () => {
  test('renders the channels and the rate limit from the per-org gateway config', async () => {
    const spy = mockGateway();
    renderPage('org-1');
    expect(screen.getByRole('heading', { name: 'Command Gateway' })).toBeInTheDocument();
    expect(await screen.findByText('Telegram')).toBeInTheDocument();
    expect(screen.getByText('Slack')).toBeInTheDocument();
    // 2 connected channels (rest-api + slack), 250/hour limit.
    await waitFor(() => expect(screen.getByText(/2 channels active/)).toBeInTheDocument());
    expect(screen.getByText(/Limit: 250 commands\/hour/)).toBeInTheDocument();
    expect(spy.mock.calls.some(([u]) => String(u).endsWith('/v1/gateway/org-1/config'))).toBe(true);
  });

  test('falls back to static channels when the config request fails', async () => {
    vi.spyOn(globalThis, 'fetch').mockResolvedValue(
      new Response(JSON.stringify({ detail: 'nope' }), { status: 503, headers: { 'Content-Type': 'application/json' } }),
    );
    renderPage();
    // Static defaults still list every channel, including WhatsApp and Discord.
    expect(await screen.findByText('WhatsApp')).toBeInTheDocument();
    expect(screen.getByText('Discord')).toBeInTheDocument();
  });

  test('Connect explains channel setup is not available and posts no credentials', async () => {
    // Regression: the modal POSTed tokens to a non-existent
    // /v1/gateway/channels/{id}/connect, failed silently, and promised the
    // token was encrypted at rest.
    const spy = mockGateway();
    renderPage('org-1');
    await screen.findByText('Telegram');
    await userEvent.click(screen.getByRole('button', { name: 'Connect Telegram' }));
    const dialog = await screen.findByRole('dialog');
    expect(dialog).toHaveTextContent(/not available/i);
    expect(screen.queryByLabelText('Bot Token')).not.toBeInTheDocument();
    expect(dialog).not.toHaveTextContent(/encrypted at rest/i);
    await userEvent.click(screen.getByRole('button', { name: 'Close' }));
    expect(spy.mock.calls.some(([u]) => String(u).includes('/connect'))).toBe(false);
  });

  test('reads the org from the route when no prop is given', async () => {
    const spy = mockGateway();
    const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    render(
      <QueryClientProvider client={qc}>
        <MemoryRouter initialEntries={['/org/org-42/gateway']}>
          <Routes><Route path="/org/:orgId/gateway" element={<GatewaySettingsPage />} /></Routes>
        </MemoryRouter>
      </QueryClientProvider>,
    );
    expect(await screen.findByRole('button', { name: /Emergency stop/i })).toBeInTheDocument();
    await waitFor(() =>
      expect(spy.mock.calls.some(([u]) => String(u).endsWith('/v1/gateway/org-42/config'))).toBe(true),
    );
  });

  test("the server's 501 reason is shown and no settings are invented", async () => {
    vi.spyOn(globalThis, 'fetch').mockImplementation(async () =>
      new Response(JSON.stringify({ detail: 'Gateway channel configuration is not implemented' }), {
        status: 501,
        headers: { 'Content-Type': 'application/json' },
      }));
    renderPage('org-1');
    expect((await screen.findAllByRole('alert'))[0]).toHaveTextContent(/not implemented/i);
    expect(screen.queryByText('100/h')).not.toBeInTheDocument();
    expect(screen.queryByText('change-autonomy')).not.toBeInTheDocument();
    expect(screen.getAllByText('Unknown').length).toBeGreaterThanOrEqual(2);
  });

  test('the emergency stop banner POSTs to the emergency-stop endpoint', async () => {
    const spy = mockGateway();
    renderPage('org-7');
    await screen.findByText('Telegram');
    await userEvent.click(screen.getByRole('button', { name: /Emergency stop/i }));
    await waitFor(() =>
      expect(spy.mock.calls.some(([u, i]) =>
        String(u).includes('/v1/org/org-7/emergency-stop') && (i as RequestInit)?.method === 'POST')).toBe(true),
    );
  });

  test('an unavailable config is shown as unknown, not as an invented one', async () => {
    // Regression: the query's catch fabricated a config (REST "connected",
    // 100/hour limit) whenever /v1/gateway/config failed — which is always.
    vi.spyOn(globalThis, 'fetch').mockImplementation(async () =>
      new Response(JSON.stringify({ detail: 'Not Found' }), { status: 404, headers: { 'Content-Type': 'application/json' } }));
    renderPage('org-1');
    expect((await screen.findAllByRole('alert'))[0]).toHaveTextContent(/configuration is unavailable/i);
    expect(screen.queryByText(/commands\/hour/)).not.toBeInTheDocument();
    expect(screen.queryByLabelText('Connected')).not.toBeInTheDocument();
  });
});
