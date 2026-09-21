import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter, Route, Routes } from 'react-router-dom';
import { afterEach, beforeEach, describe, expect, test, vi } from 'vitest';
import { useAuthStore } from '@/stores/auth';
import { AuthPage } from './AuthPage';

function renderAuthPage() {
  render(
    <MemoryRouter initialEntries={['/auth']}>
      <Routes>
        <Route path="/auth" element={<AuthPage />} />
        <Route path="/auth/mfa" element={<div>MFA Page</div>} />
        <Route path="/dashboard" element={<div>Dashboard</div>} />
      </Routes>
    </MemoryRouter>
  );
}

/** Builds a fetch mock that dispatches on URL, so a single test can stub
 *  /auth/config, /tenants/me and /auth/mfa/status independently. */
function mockFetchByUrl(handlers: Record<string, () => Response | Promise<Response>>) {
  return vi.spyOn(globalThis, 'fetch').mockImplementation(async (input) => {
    const url = String(input);
    for (const [match, handler] of Object.entries(handlers)) {
      if (url.includes(match)) return handler();
    }
    // Default: SSO config disabled, so tests that don't care about it aren't affected.
    return new Response(JSON.stringify({ sso_enabled: false, authorization_endpoint: null }), {
      status: 200,
      headers: { 'Content-Type': 'application/json' },
    });
  });
}

function jsonResponse(body: unknown, status = 200) {
  return new Response(JSON.stringify(body), {
    status,
    headers: { 'Content-Type': 'application/json' },
  });
}

describe('AuthPage', () => {
  beforeEach(() => {
    localStorage.clear();
    sessionStorage.clear();
    useAuthStore.setState({
      apiKey: '',
      tenantId: '',
      plan: '',
      isAuthenticated: false,
      mfaRequired: false,
    });
  });

  afterEach(() => {
    vi.restoreAllMocks();
  });

  test('does not login when the API key is rejected by the backend', async () => {
    vi.spyOn(globalThis, 'fetch').mockResolvedValue(
      new Response(JSON.stringify({ error: { message: 'Missing or invalid API key.' } }), {
        status: 401,
        headers: { 'Content-Type': 'application/json' },
      })
    );

    renderAuthPage();

    await userEvent.type(screen.getByLabelText(/tenant id/i), 'tenant-1');
    await userEvent.type(screen.getByLabelText(/^api key$/i), 'bad-key');
    await userEvent.click(screen.getByRole('button', { name: /sign in/i }));

    expect(await screen.findByRole('alert')).toHaveTextContent('Invalid tenant ID or API key.');
    expect(screen.queryByText('Dashboard')).not.toBeInTheDocument();
    expect(localStorage.getItem('av_api_key')).toBeNull();
    expect(useAuthStore.getState().isAuthenticated).toBe(false);
  });

  test('shows a validation error when tenant ID and API key are blank', async () => {
    mockFetchByUrl({});
    renderAuthPage();

    // Bypass native `required` validation (fireEvent.submit skips browser
    // constraint validation, unlike a real click / Enter keypress).
    const form = screen.getByRole('button', { name: /sign in/i }).closest('form');
    expect(form).not.toBeNull();
    fireEvent.submit(form as HTMLFormElement);

    expect(await screen.findByRole('alert')).toHaveTextContent(
      'API key and tenant ID are required.'
    );
    expect(useAuthStore.getState().isAuthenticated).toBe(false);
  });

  test('shows an error when the tenant ID does not match the authenticated tenant', async () => {
    mockFetchByUrl({
      '/tenants/me': () => jsonResponse({ tenant_id: 'other-tenant', plan: 'free' }),
    });
    renderAuthPage();

    await userEvent.type(screen.getByLabelText(/tenant id/i), 'my-tenant');
    await userEvent.type(screen.getByLabelText(/^api key$/i), 'av_key_123');
    await userEvent.click(screen.getByRole('button', { name: /sign in/i }));

    expect(await screen.findByRole('alert')).toHaveTextContent('Invalid tenant ID or API key.');
    expect(useAuthStore.getState().isAuthenticated).toBe(false);
  });

  test('shows a network error message when the backend is unreachable', async () => {
    vi.spyOn(globalThis, 'fetch').mockImplementation(async (input) => {
      const url = String(input);
      if (url.includes('/auth/config')) {
        return jsonResponse({ sso_enabled: false, authorization_endpoint: null });
      }
      throw new Error('network down');
    });
    renderAuthPage();

    await userEvent.type(screen.getByLabelText(/tenant id/i), 'my-tenant');
    await userEvent.type(screen.getByLabelText(/^api key$/i), 'av_key_123');
    await userEvent.click(screen.getByRole('button', { name: /sign in/i }));

    expect(await screen.findByRole('alert')).toHaveTextContent(
      'Unable to reach the backend. Please try again.'
    );
    expect(useAuthStore.getState().isAuthenticated).toBe(false);
  });

  test('logs in and navigates straight to the dashboard when MFA is not enabled', async () => {
    mockFetchByUrl({
      '/tenants/me': () => jsonResponse({ tenant_id: 'my-tenant', plan: 'starter' }),
      '/auth/mfa/status': () =>
        jsonResponse({ enabled: false, has_pending_enrollment: false, recovery_codes_count: 0 }),
    });
    renderAuthPage();

    await userEvent.type(screen.getByLabelText(/tenant id/i), 'my-tenant');
    await userEvent.type(screen.getByLabelText(/^api key$/i), 'av_key_123');
    await userEvent.click(screen.getByRole('button', { name: /sign in/i }));

    expect(await screen.findByText('Dashboard')).toBeInTheDocument();
    expect(useAuthStore.getState().isAuthenticated).toBe(true);
    expect(useAuthStore.getState().mfaRequired).toBe(false);
  });

  test('redirects to the MFA challenge when the tenant has MFA enabled', async () => {
    mockFetchByUrl({
      '/tenants/me': () => jsonResponse({ tenant_id: 'my-tenant', plan: 'enterprise' }),
      '/auth/mfa/status': () =>
        jsonResponse({ enabled: true, has_pending_enrollment: false, recovery_codes_count: 5 }),
    });
    renderAuthPage();

    await userEvent.type(screen.getByLabelText(/tenant id/i), 'my-tenant');
    await userEvent.type(screen.getByLabelText(/^api key$/i), 'av_key_123');
    await userEvent.click(screen.getByRole('button', { name: /sign in/i }));

    expect(await screen.findByText('MFA Page')).toBeInTheDocument();
    expect(useAuthStore.getState().mfaRequired).toBe(true);
  });

  test('proceeds to the dashboard when the MFA status endpoint is unavailable', async () => {
    mockFetchByUrl({
      '/tenants/me': () => jsonResponse({ tenant_id: 'my-tenant', plan: 'free' }),
      '/auth/mfa/status': () => jsonResponse({ detail: 'not found' }, 404),
    });
    renderAuthPage();

    await userEvent.type(screen.getByLabelText(/tenant id/i), 'my-tenant');
    await userEvent.type(screen.getByLabelText(/^api key$/i), 'av_key_123');
    await userEvent.click(screen.getByRole('button', { name: /sign in/i }));

    expect(await screen.findByText('Dashboard')).toBeInTheDocument();
    expect(useAuthStore.getState().isAuthenticated).toBe(true);
  });

  test('toggles the API key field between masked and visible text', async () => {
    mockFetchByUrl({});
    renderAuthPage();

    const apiKeyInput = screen.getByLabelText(/^api key$/i);
    expect(apiKeyInput).toHaveAttribute('type', 'password');

    await userEvent.click(screen.getByRole('button', { name: /show api key/i }));
    expect(apiKeyInput).toHaveAttribute('type', 'text');

    await userEvent.click(screen.getByRole('button', { name: /hide api key/i }));
    expect(apiKeyInput).toHaveAttribute('type', 'password');
  });

  test('opens a mailto link when "Request access" is clicked', async () => {
    mockFetchByUrl({});
    const openSpy = vi.spyOn(window, 'open').mockImplementation(() => null);
    renderAuthPage();

    await userEvent.click(screen.getByRole('button', { name: /request access/i }));

    expect(openSpy).toHaveBeenCalledWith(
      'mailto:hello@agentverse.ai?subject=Access Request',
      '_blank'
    );
  });

  test('shows the Keycloak SSO button when the backend reports SSO is enabled, and starts the flow', async () => {
    mockFetchByUrl({
      '/auth/config': () =>
        jsonResponse({ sso_enabled: true, authorization_endpoint: 'https://kc.example/auth' }),
    });

    const originalLocation = window.location;
    // jsdom doesn't implement navigation; replace `location` with a writable
    // stand-in so clicking the SSO button doesn't throw.
    Object.defineProperty(window, 'location', {
      configurable: true,
      value: { ...originalLocation, href: originalLocation.href },
    });

    try {
      renderAuthPage();

      const ssoButton = await screen.findByRole('button', { name: /sign in with keycloak sso/i });
      // The API-key form still renders below the SSO button and divider.
      expect(screen.getByLabelText(/^api key$/i)).toBeInTheDocument();

      await userEvent.click(ssoButton);

      expect(sessionStorage.getItem('av_sso_state')).not.toBeNull();
      expect(window.location.href).toContain('/auth/login?redirect_uri=');
      expect(window.location.href).toContain('state=');
    } finally {
      Object.defineProperty(window, 'location', {
        configurable: true,
        value: originalLocation,
      });
    }
  });

  test('does not show the Keycloak SSO button when the SSO config probe fails', async () => {
    vi.spyOn(globalThis, 'fetch').mockImplementation(async (input) => {
      const url = String(input);
      if (url.includes('/auth/config')) throw new Error('network down');
      return jsonResponse({});
    });
    renderAuthPage();

    await waitFor(() => {
      expect(screen.queryByRole('button', { name: /sign in with keycloak sso/i })).toBeNull();
    });
    // Form still renders normally despite the failed SSO probe.
    expect(screen.getByLabelText(/^api key$/i)).toBeInTheDocument();
  });

  test('does not show the Keycloak SSO button when SSO is disabled', async () => {
    mockFetchByUrl({
      '/auth/config': () => jsonResponse({ sso_enabled: false, authorization_endpoint: null }),
    });
    renderAuthPage();

    await waitFor(() => {
      expect(screen.queryByRole('button', { name: /sign in with keycloak sso/i })).toBeNull();
    });
  });
});
