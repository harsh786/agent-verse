import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter } from 'react-router-dom';
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
    if (url.includes('/gateway/config') && method === 'GET')
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
  test('renders the channels and the rate limit from the gateway config', async () => {
    mockGateway();
    renderPage();
    expect(screen.getByRole('heading', { name: 'Command Gateway' })).toBeInTheDocument();
    expect(await screen.findByText('Telegram')).toBeInTheDocument();
    expect(screen.getByText('Slack')).toBeInTheDocument();
    // 2 connected channels (rest-api + slack), 250/hour limit.
    await waitFor(() => expect(screen.getByText(/2 channels active/)).toBeInTheDocument());
    expect(screen.getByText(/Limit: 250 commands\/hour/)).toBeInTheDocument();
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

  test('connecting a channel opens the modal and POSTs the token', async () => {
    const spy = mockGateway();
    renderPage();
    await screen.findByText('Telegram');
    await userEvent.click(screen.getByRole('button', { name: 'Connect Telegram' }));
    // Modal shows and asks for a bot token.
    expect(await screen.findByRole('dialog')).toBeInTheDocument();
    await userEvent.type(screen.getByLabelText('Bot Token'), 'secret-token');
    await userEvent.click(screen.getByRole('button', { name: 'Connect' }));
    await waitFor(() =>
      expect(spy.mock.calls.some(([u, i]) => {
        if (!String(u).includes('/gateway/channels/telegram/connect') || (i as RequestInit)?.method !== 'POST') return false;
        return JSON.parse((i as RequestInit).body as string).token === 'secret-token';
      })).toBe(true),
    );
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
});
