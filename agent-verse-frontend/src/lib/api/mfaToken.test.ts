/**
 * MFA end to end: the X-MFA-Token issued by /auth/mfa/verify must be kept and
 * sent on every request. MFAVerifyPage used to discard it (setMfaToken(null))
 * and no transport ever sent X-MFA-Token, so with MFA enforcement on every
 * call after verification answered 401 MFA_REQUIRED.
 */
import { afterEach, beforeEach, describe, expect, test, vi } from 'vitest';
import { useAuthStore, getAuthHeader, getMfaHeader, mfaSubprotocol } from '@/stores/auth';
import { downloadAuthenticated, mfaApi } from './client';

function jsonResponse(status: number, body: unknown): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { 'Content-Type': 'application/json' },
  });
}

type FetchSpy = { mock: { calls: unknown[][] } };

function headersOf(spy: FetchSpy, index = 0): Record<string, string> {
  const init = spy.mock.calls[index]?.[1] as RequestInit | undefined;
  return (init?.headers ?? {}) as Record<string, string>;
}

beforeEach(() => {
  sessionStorage.clear();
  localStorage.clear();
  useAuthStore.setState({
    apiKey: 'k',
    tenantId: 't',
    plan: 'free',
    isAuthenticated: true,
    ssoMode: false,
    accessToken: '',
    mfaRequired: false,
    mfaToken: null,
  });
});
afterEach(() => vi.restoreAllMocks());

describe('X-MFA-Token transport', () => {
  test('request() sends X-MFA-Token once a session token is stored', async () => {
    const spy = vi.spyOn(globalThis, 'fetch').mockResolvedValue(
      jsonResponse(200, { enabled: true, has_pending_enrollment: false, recovery_codes_count: 3 }),
    );
    await mfaApi.status();
    expect(headersOf(spy)['X-MFA-Token']).toBeUndefined();

    useAuthStore.getState().setMfaToken('mfa-session-1');
    spy.mockResolvedValue(jsonResponse(200, { enabled: true }));
    await mfaApi.status();
    expect(headersOf(spy, 1)['X-MFA-Token']).toBe('mfa-session-1');
    expect(headersOf(spy, 1)['X-API-Key']).toBe('k');
  });

  test('getAuthHeader() (raw fetch call sites) carries the MFA token', () => {
    useAuthStore.getState().setMfaToken('mfa-session-2');
    expect(getAuthHeader()).toEqual({ 'X-API-Key': 'k', 'X-MFA-Token': 'mfa-session-2' });
    expect(getMfaHeader()).toEqual({ 'X-MFA-Token': 'mfa-session-2' });
  });

  test('downloadAuthenticated() carries the MFA token', async () => {
    useAuthStore.getState().setMfaToken('mfa-session-3');
    const spy = vi.spyOn(globalThis, 'fetch').mockResolvedValue(new Response('x', { status: 200 }));
    await downloadAuthenticated('/exports/1');
    expect(headersOf(spy)['X-MFA-Token']).toBe('mfa-session-3');
  });

  test('WebSocket subprotocol form is av.mfa.<base64url(token)>', () => {
    expect(mfaSubprotocol()).toBeNull();
    useAuthStore.getState().setMfaToken('tok/+=');
    const proto = mfaSubprotocol();
    expect(proto).toMatch(/^av\.mfa\.[A-Za-z0-9_-]+$/);
    const encoded = (proto as string).slice('av.mfa.'.length).replace(/-/g, '+').replace(/_/g, '/');
    expect(atob(encoded + '='.repeat((4 - (encoded.length % 4)) % 4))).toBe('tok/+=');
  });

  test('logout forgets the MFA session token', () => {
    useAuthStore.getState().setMfaToken('mfa-session-4');
    useAuthStore.getState().logout();
    expect(useAuthStore.getState().mfaToken).toBeNull();
    expect(getMfaHeader()).toEqual({});
  });

  test('a 401 MFA_SESSION_EXPIRED re-prompts for MFA instead of logging out', async () => {
    useAuthStore.getState().setMfaToken('stale');
    vi.spyOn(globalThis, 'fetch').mockResolvedValue(
      jsonResponse(401, { error: { code: 'MFA_SESSION_EXPIRED', message: 'expired' } }),
    );
    await expect(mfaApi.status()).rejects.toMatchObject({ status: 401 });
    const s = useAuthStore.getState();
    expect(s.mfaToken).toBeNull();
    expect(s.mfaRequired).toBe(true);
    expect(s.isAuthenticated).toBe(true);
    expect(s.apiKey).toBe('k');
  });

  test('a plain 401 still logs out', async () => {
    vi.spyOn(globalThis, 'fetch').mockResolvedValue(
      jsonResponse(401, { error: { code: 'AUTHENTICATION_ERROR', message: 'bad key' } }),
    );
    await expect(mfaApi.status()).rejects.toMatchObject({ status: 401 });
    expect(useAuthStore.getState().isAuthenticated).toBe(false);
  });
});
