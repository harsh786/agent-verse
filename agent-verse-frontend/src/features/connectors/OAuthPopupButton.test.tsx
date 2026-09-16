/**
 * Tests for OAuthPopupButton — opens an OAuth popup and completes the flow from
 * the postMessage the popup sends back.
 */
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { render, screen, fireEvent, waitFor, act } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, test, vi } from 'vitest';
import { useAuthStore } from '@/stores/auth';
import { OAuthPopupButton } from './OAuthPopupButton';

const AUTH_URL = 'https://github.com/login/oauth/authorize?client_id=x';

function mockFetch() {
  return vi.spyOn(globalThis, 'fetch').mockImplementation(async (input, init) => {
    const url = String(input);
    const method = (init?.method ?? 'GET').toUpperCase();
    if (url.includes('/connectors/oauth/start') && method === 'POST')
      return new Response(JSON.stringify({ auth_url: AUTH_URL, state: 'state-xyz' }), { status: 200, headers: { 'Content-Type': 'application/json' } });
    if (url.includes('/connectors/oauth/callback') && method === 'POST')
      return new Response(JSON.stringify({ server_id: 'srv-1', name: 'github', status: 'active' }), { status: 200, headers: { 'Content-Type': 'application/json' } });
    return new Response('{}', { status: 200, headers: { 'Content-Type': 'application/json' } });
  });
}

function renderButton(props: Partial<React.ComponentProps<typeof OAuthPopupButton>> = {}) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } });
  return render(
    <QueryClientProvider client={qc}>
      <OAuthPopupButton connectorName="github" displayName="GitHub" {...props} />
    </QueryClientProvider>,
  );
}

function postCallback(data: Record<string, unknown>) {
  act(() => {
    window.dispatchEvent(new MessageEvent('message', { data, origin: window.location.origin }));
  });
}

beforeEach(() => {
  sessionStorage.clear();
  localStorage.clear();
  useAuthStore.setState({ apiKey: 'k', tenantId: 't', plan: 'free', isAuthenticated: true, ssoMode: false, accessToken: '' });
});
afterEach(() => vi.restoreAllMocks());

describe('OAuthPopupButton', () => {
  test('renders an idle "Connect with" button using the display name', () => {
    renderButton();
    expect(screen.getByRole('button', { name: /Connect with GitHub/i })).toBeInTheDocument();
  });

  test('clicking starts the OAuth flow and opens a popup at the provider auth URL', async () => {
    const spy = mockFetch();
    const fakePopup = { closed: false, close: vi.fn() } as unknown as Window;
    const openSpy = vi.spyOn(window, 'open').mockReturnValue(fakePopup);
    renderButton();

    fireEvent.click(screen.getByRole('button', { name: /Connect with GitHub/i }));

    await waitFor(() =>
      expect(spy.mock.calls.some(([u, i]) => String(u).includes('/connectors/oauth/start') && (i as RequestInit)?.method === 'POST')).toBe(true),
    );
    await waitFor(() => expect(openSpy).toHaveBeenCalled());
    expect(openSpy.mock.calls[0][0]).toBe(AUTH_URL);
    expect(await screen.findByText(/Waiting for authorization/i)).toBeInTheDocument();
  });

  test('surfaces a blocked popup (window.open returns null) as a recoverable error', async () => {
    mockFetch();
    vi.spyOn(window, 'open').mockReturnValue(null);
    renderButton();
    fireEvent.click(screen.getByRole('button', { name: /Connect with GitHub/i }));
    // It never enters the "waiting" state and the button returns to a connectable label.
    await waitFor(() => expect(screen.queryByText(/Waiting for authorization/i)).not.toBeInTheDocument());
    expect(screen.getByRole('button', { name: /Connect with GitHub/i })).toBeInTheDocument();
  });

  test('completes the flow when the popup posts a matching code + state', async () => {
    const spy = mockFetch();
    const fakePopup = { closed: false, close: vi.fn() } as unknown as Window;
    vi.spyOn(window, 'open').mockReturnValue(fakePopup);
    const onSuccess = vi.fn();
    renderButton({ onSuccess });

    fireEvent.click(screen.getByRole('button', { name: /Connect with GitHub/i }));
    await screen.findByText(/Waiting for authorization/i);

    postCallback({ type: 'oauth_callback', code: 'code-123', state: 'state-xyz' });

    await waitFor(() =>
      expect(spy.mock.calls.some(([u, i]) => String(u).includes('/connectors/oauth/callback') && (i as RequestInit)?.method === 'POST')).toBe(true),
    );
    await waitFor(() => expect(onSuccess).toHaveBeenCalledWith('srv-1'));
    expect(await screen.findByText('Connected')).toBeInTheDocument();
  });

  test('rejects a state mismatch and never calls the callback endpoint', async () => {
    const spy = mockFetch();
    const fakePopup = { closed: false, close: vi.fn() } as unknown as Window;
    vi.spyOn(window, 'open').mockReturnValue(fakePopup);
    renderButton();

    fireEvent.click(screen.getByRole('button', { name: /Connect with GitHub/i }));
    await screen.findByText(/Waiting for authorization/i);

    postCallback({ type: 'oauth_callback', code: 'code-123', state: 'WRONG' });

    // Give any (unexpected) callback request a chance to fire, then assert none did.
    await new Promise((r) => setTimeout(r, 50));
    expect(spy.mock.calls.some(([u]) => String(u).includes('/connectors/oauth/callback'))).toBe(false);
  });
});
