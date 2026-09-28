/**
 * Tests for OAuthPopupButton — runs the backend's real PKCE flow
 * (GET /connectors/oauth/start?server_id → popup → GET /connectors/oauth/callback)
 * and only reports "Connected" when the backend says tokens were stored.
 */
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { render, screen, fireEvent, waitFor, act } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, test, vi } from 'vitest';
import { useAuthStore } from '@/stores/auth';
import { useToastStore } from '@/stores/toast';
import { OAuthPopupButton } from './OAuthPopupButton';

const AUTH_URL = 'https://github.com/login/oauth/authorize?client_id=x&code_challenge=c';
const REDIRECT_URI = `${window.location.origin}/connectors/oauth/callback`;

function json(body: unknown, status = 200) {
  return new Response(JSON.stringify(body), { status, headers: { 'Content-Type': 'application/json' } });
}

function mockFetch(opts: {
  start?: () => Response;
  callback?: () => Response;
} = {}) {
  return vi.spyOn(globalThis, 'fetch').mockImplementation(async (input, init) => {
    const url = String(input);
    const method = (init?.method ?? 'GET').toUpperCase();
    if (url.includes('/connectors/oauth/start') && method === 'GET')
      return opts.start
        ? opts.start()
        : json({ server_id: 'srv-1', auth_url: AUTH_URL, state: 'state-xyz', redirect_uri: REDIRECT_URI });
    if (url.includes('/connectors/oauth/callback') && method === 'GET')
      return opts.callback
        ? opts.callback()
        : json({ server_id: 'srv-1', status: 'connected', message: 'OAuth tokens stored securely in credential vault.' });
    return json({});
  });
}

function renderButton(props: Partial<React.ComponentProps<typeof OAuthPopupButton>> = {}) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } });
  return render(
    <QueryClientProvider client={qc}>
      <OAuthPopupButton connectorName="github" displayName="GitHub" serverId="srv-1" {...props} />
    </QueryClientProvider>,
  );
}

function postCallback(data: Record<string, unknown>) {
  act(() => {
    window.dispatchEvent(new MessageEvent('message', { data, origin: window.location.origin }));
  });
}

async function startAndWait() {
  fireEvent.click(screen.getByRole('button', { name: /Connect with GitHub/i }));
  await screen.findByText(/Waiting for authorization/i);
}

function errorToasts() {
  return useToastStore.getState().toasts.filter((t) => t.kind === 'error').map((t) => t.message);
}

beforeEach(() => {
  sessionStorage.clear();
  localStorage.clear();
  useToastStore.setState({ toasts: [] });
  useAuthStore.setState({ apiKey: 'k', tenantId: 't', plan: 'free', isAuthenticated: true, ssoMode: false, accessToken: '' });
});
afterEach(() => vi.restoreAllMocks());

describe('OAuthPopupButton', () => {
  test('renders an idle "Connect with" button using the display name', () => {
    renderButton();
    expect(screen.getByRole('button', { name: /Connect with GitHub/i })).toBeEnabled();
  });

  test('without a registered connector it is disabled and explains to configure first', () => {
    const spy = mockFetch();
    renderButton({ serverId: undefined });
    expect(screen.getByRole('button', { name: /Connect with GitHub/i })).toBeDisabled();
    expect(screen.getByText(/Configure this connector first/i)).toBeInTheDocument();
    expect(spy).not.toHaveBeenCalled();
  });

  test('clicking starts the PKCE flow (GET start?server_id) and opens a popup at the provider auth URL', async () => {
    const spy = mockFetch();
    const fakePopup = { closed: false, close: vi.fn() } as unknown as Window;
    const openSpy = vi.spyOn(window, 'open').mockReturnValue(fakePopup);
    renderButton();

    fireEvent.click(screen.getByRole('button', { name: /Connect with GitHub/i }));

    await waitFor(() => expect(openSpy).toHaveBeenCalled());
    const startCall = spy.mock.calls.find(([u]) => String(u).includes('/connectors/oauth/start'));
    expect(String(startCall?.[0])).toContain('server_id=srv-1');
    expect((startCall?.[1] as RequestInit | undefined)?.method ?? 'GET').toBe('GET');
    // The legacy POST start is never used.
    expect(spy.mock.calls.some(([, i]) => (i as RequestInit)?.method === 'POST')).toBe(false);
    expect(openSpy.mock.calls[0][0]).toBe(AUTH_URL);
    expect(await screen.findByText(/Waiting for authorization/i)).toBeInTheDocument();
  });

  test('surfaces a blocked popup (window.open returns null) as a recoverable error', async () => {
    mockFetch();
    vi.spyOn(window, 'open').mockReturnValue(null);
    renderButton();
    fireEvent.click(screen.getByRole('button', { name: /Connect with GitHub/i }));
    expect(await screen.findByRole('alert')).toHaveTextContent(/Popup blocked/i);
    expect(screen.queryByText(/Waiting for authorization/i)).not.toBeInTheDocument();
    expect(screen.getByRole('button', { name: /Connect with GitHub/i })).toBeEnabled();
  });

  test('refuses to open a popup when the connector has no authorize URL configured', async () => {
    mockFetch({
      start: () => json({
        server_id: 'srv-1',
        auth_url: 'Configure authorize_url and client_id in auth_config for connector srv-1',
        state: 's',
        redirect_uri: REDIRECT_URI,
      }),
    });
    const openSpy = vi.spyOn(window, 'open');
    renderButton();
    fireEvent.click(screen.getByRole('button', { name: /Connect with GitHub/i }));
    expect(await screen.findByRole('alert')).toHaveTextContent(/Configure authorize_url and client_id/);
    expect(openSpy).not.toHaveBeenCalled();
  });

  test('refuses to open a popup whose redirect URI cannot return to this app', async () => {
    mockFetch({
      start: () => json({
        server_id: 'srv-1',
        auth_url: AUTH_URL,
        state: 's',
        redirect_uri: 'http://api.example.com/connectors/oauth/callback?server_id=srv-1',
      }),
    });
    const openSpy = vi.spyOn(window, 'open');
    renderButton();
    fireEvent.click(screen.getByRole('button', { name: /Connect with GitHub/i }));
    expect(await screen.findByRole('alert')).toHaveTextContent(/redirect URI/i);
    expect(openSpy).not.toHaveBeenCalled();
  });

  test('completes via GET /connectors/oauth/callback and shows Connected only on status "connected"', async () => {
    const spy = mockFetch();
    vi.spyOn(window, 'open').mockReturnValue({ closed: false, close: vi.fn() } as unknown as Window);
    const onSuccess = vi.fn();
    renderButton({ onSuccess });

    await startAndWait();
    postCallback({ type: 'oauth_callback', code: 'code-123', state: 'state-xyz' });

    await waitFor(() => expect(onSuccess).toHaveBeenCalledWith('srv-1'));
    const cb = spy.mock.calls.find(([u]) => String(u).includes('/connectors/oauth/callback'));
    const cbUrl = String(cb?.[0]);
    expect(cbUrl).toContain('server_id=srv-1');
    expect(cbUrl).toContain('code=code-123');
    expect(cbUrl).toContain('state=state-xyz');
    expect(await screen.findByText('Connected')).toBeInTheDocument();
  });

  test('a non-"connected" callback status (e.g. pending_config) is an error, not a connection', async () => {
    mockFetch({
      callback: () => json({ server_id: 'srv-1', status: 'pending_config', message: 'Token URL not configured for this connector.' }),
    });
    vi.spyOn(window, 'open').mockReturnValue({ closed: false, close: vi.fn() } as unknown as Window);
    const onSuccess = vi.fn();
    renderButton({ onSuccess });

    await startAndWait();
    postCallback({ type: 'oauth_callback', code: 'code-123', state: 'state-xyz' });

    expect(await screen.findByRole('alert')).toHaveTextContent(/Token URL not configured/);
    expect(screen.queryByText('Connected')).not.toBeInTheDocument();
    expect(onSuccess).not.toHaveBeenCalled();
    expect(errorToasts().some((m) => m.includes('Token URL not configured'))).toBe(true);
  });

  test('a 501 oauth-token-exchange-unavailable answer is surfaced as NOT connected', async () => {
    mockFetch({
      callback: () => json({
        detail: {
          type: 'oauth-token-exchange-unavailable',
          code: 'oauth-token-exchange-unavailable',
          title: 'OAuth token exchange not available for popup flow',
          detail: "The authorization code for 'github' was not exchanged and no connector was created.",
          connected: false,
        },
      }, 501),
    });
    vi.spyOn(window, 'open').mockReturnValue({ closed: false, close: vi.fn() } as unknown as Window);
    const onSuccess = vi.fn();
    renderButton({ onSuccess });

    await startAndWait();
    postCallback({ type: 'oauth_callback', code: 'code-123', state: 'state-xyz' });

    expect(await screen.findByRole('alert')).toHaveTextContent(/NOT connected/);
    expect(screen.queryByText('Connected')).not.toBeInTheDocument();
    expect(onSuccess).not.toHaveBeenCalled();
    expect(useToastStore.getState().toasts.some((t) => t.kind === 'success')).toBe(false);
  });

  test('a provider error relayed by the popup is shown and nothing is exchanged', async () => {
    const spy = mockFetch();
    vi.spyOn(window, 'open').mockReturnValue({ closed: false, close: vi.fn() } as unknown as Window);
    renderButton();

    await startAndWait();
    postCallback({ type: 'oauth_callback', error: 'access_denied' });

    expect(await screen.findByRole('alert')).toHaveTextContent(/access_denied/);
    expect(spy.mock.calls.some(([u]) => String(u).includes('/connectors/oauth/callback'))).toBe(false);
  });

  test('rejects a state mismatch and never calls the callback endpoint', async () => {
    const spy = mockFetch();
    vi.spyOn(window, 'open').mockReturnValue({ closed: false, close: vi.fn() } as unknown as Window);
    renderButton();

    await startAndWait();
    postCallback({ type: 'oauth_callback', code: 'code-123', state: 'WRONG' });

    expect(await screen.findByRole('alert')).toHaveTextContent(/state mismatch/i);
    expect(spy.mock.calls.some(([u]) => String(u).includes('/connectors/oauth/callback'))).toBe(false);
  });

  test('a failed start surfaces the backend reason', async () => {
    mockFetch({ start: () => json({ detail: 'Connector cfg uses auth_type=bearer, not OAuth' }, 400) });
    renderButton();
    fireEvent.click(screen.getByRole('button', { name: /Connect with GitHub/i }));
    expect(await screen.findByRole('alert')).toHaveTextContent(/not OAuth/);
  });
});
