import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, beforeEach, describe, expect, test, vi } from 'vitest';
import { useAuthStore } from '@/stores/auth';
import { ChannelBindings } from './ChannelBindings';

function renderIt() {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={qc}>
      <ChannelBindings />
    </QueryClientProvider>
  );
}

const json = (data: unknown, status = 200) =>
  new Response(JSON.stringify(data), { status, headers: { 'Content-Type': 'application/json' } });

beforeEach(() => {
  localStorage.setItem('av_api_key', 'test-key');
  useAuthStore.setState({ apiKey: 'test-key', tenantId: 't', plan: 'free', isAuthenticated: true });
});
afterEach(() => vi.restoreAllMocks());

describe('ChannelBindings (TRG-42)', () => {
  test('lists the tenant bindings from /channels/bindings', async () => {
    vi.spyOn(globalThis, 'fetch').mockResolvedValue(
      json([
        {
          id: 'b1', channel: 'telegram', addressee: '123456', status: 'verified', routable: true,
          has_secret: true, has_outbound_token: true, webhook_url: '/v1/gateway/telegram/chat/123456',
        },
      ])
    );
    renderIt();
    expect(await screen.findByText('123456')).toBeInTheDocument();
    expect(screen.getByText('/v1/gateway/telegram/chat/123456')).toBeInTheDocument();
  });

  test('adding a webhook binding shows the generated secret once', async () => {
    const spy = vi.spyOn(globalThis, 'fetch').mockImplementation(async (_u, init) => {
      if (init?.method === 'POST')
        return json({
          id: 'b2', channel: 'webhook', addressee: 'wh-abc', status: 'verified',
          has_secret: true, has_outbound_token: false, webhook_url: '/v1/gateway/webhook/chat/wh-abc',
          secret: 'generated-secret',
        });
      return json([]);
    });
    renderIt();
    await userEvent.selectOptions(await screen.findByLabelText('Binding channel'), 'webhook');
    await userEvent.click(screen.getByRole('button', { name: 'Add binding' }));
    expect(await screen.findByText('generated-secret')).toBeInTheDocument();
    const post = spy.mock.calls.find(([, i]) => i?.method === 'POST');
    expect(String(post?.[0])).toContain('/channels/bindings');
    expect(JSON.parse(String(post?.[1]?.body)).channel).toBe('webhook');
  });

  test('DEF-3: a WhatsApp binding shows its verify token once', async () => {
    vi.spyOn(globalThis, 'fetch').mockImplementation(async (_u, init) => {
      if (init?.method === 'POST')
        return json({
          id: 'b3', channel: 'whatsapp', addressee: '1098765432', status: 'verified',
          has_secret: true, has_outbound_token: true,
          webhook_url: 'https://agents.example.com/v1/gateway/whatsapp/chat/1098765432',
          verify_token: 'vt-once',
        });
      return json([]);
    });
    renderIt();
    await userEvent.selectOptions(await screen.findByLabelText('Binding channel'), 'whatsapp');
    await userEvent.type(screen.getByLabelText('Addressee'), '1098765432');
    await userEvent.click(screen.getByRole('button', { name: 'Add binding' }));
    expect(await screen.findByText('vt-once')).toBeInTheDocument();
  });

  test('DEF-3: an unregistered Telegram webhook is reported, not hidden', async () => {
    vi.spyOn(globalThis, 'fetch').mockImplementation(async (_u, init) => {
      if (init?.method === 'POST')
        return json({
          id: 'b4', channel: 'telegram', addressee: '123456', status: 'verified',
          has_secret: true, has_outbound_token: true, webhook_url: '/v1/gateway/telegram/chat/123456',
          secret: 'gen', webhook_registered: false,
          webhook_registration: 'Not registered: the server has no GATEWAY_PUBLIC_BASE_URL.',
        });
      return json([]);
    });
    renderIt();
    await userEvent.type(await screen.findByLabelText('Addressee'), '123456');
    await userEvent.click(screen.getByRole('button', { name: 'Add binding' }));
    expect(await screen.findByText(/no GATEWAY_PUBLIC_BASE_URL/)).toBeInTheDocument();
  });

  test("a refused binding shows the server's reason (no optimistic success)", async () => {
    vi.spyOn(globalThis, 'fetch').mockImplementation(async (_u, init) => {
      if (init?.method === 'POST')
        return json({ detail: 'Ownership not proven: not the bot token' }, 422);
      return json([]);
    });
    renderIt();
    await userEvent.type(await screen.findByLabelText('Addressee'), '123456');
    await userEvent.click(screen.getByRole('button', { name: 'Add binding' }));
    expect(await screen.findByRole('alert')).toHaveTextContent(/ownership not proven/i);
  });

  test('remove deletes the binding', async () => {
    const spy = vi.spyOn(globalThis, 'fetch').mockImplementation(async (_u, init) => {
      if (init?.method === 'DELETE') return json({ deleted: true });
      return json([
        {
          id: 'b9', channel: 'slack', addressee: 'TACME01', status: 'verified',
          has_secret: true, has_outbound_token: true, webhook_url: '/v1/gateway/slack/chat/TACME01',
        },
      ]);
    });
    renderIt();
    await userEvent.click(await screen.findByRole('button', { name: /Remove slack binding TACME01/ }));
    await waitFor(() =>
      expect(
        spy.mock.calls.some(([u, i]) => String(u).includes('/channels/bindings/b9') && i?.method === 'DELETE')
      ).toBe(true)
    );
  });
});
