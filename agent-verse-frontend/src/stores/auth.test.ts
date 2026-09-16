import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { useAuthStore, getAuthHeader, isTokenExpired } from './auth';

function resetStore() {
  useAuthStore.setState({
    apiKey: '',
    tenantId: '',
    plan: '',
    isAuthenticated: false,
    ssoMode: false,
    accessToken: '',
    refreshToken: '',
    tokenExpiresAt: 0,
    sessionValidated: false,
    mfaRequired: false,
    mfaToken: null,
  });
}

beforeEach(() => {
  sessionStorage.clear();
  localStorage.clear();
  resetStore();
});

afterEach(() => {
  vi.useRealTimers();
});

describe('useAuthStore actions', () => {
  it('setCredentials sets API-key auth state and clears SSO fields', () => {
    useAuthStore.getState().setCredentials('key-123', 'tenant-1', 'pro');
    const s = useAuthStore.getState();
    expect(s.apiKey).toBe('key-123');
    expect(s.tenantId).toBe('tenant-1');
    expect(s.plan).toBe('pro');
    expect(s.isAuthenticated).toBe(true);
    expect(s.ssoMode).toBe(false);
    expect(s.accessToken).toBe('');
    expect(s.refreshToken).toBe('');
    expect(s.tokenExpiresAt).toBe(0);
  });

  it('setCredentials writes the api key to sessionStorage and clears any legacy localStorage copy', () => {
    localStorage.setItem('av_api_key', 'stale-key');
    useAuthStore.getState().setCredentials('key-123', 'tenant-1', 'pro');
    expect(sessionStorage.getItem('av_api_key')).toBe('key-123');
    expect(localStorage.getItem('av_api_key')).toBeNull();
  });

  it('setSSOCredentials sets JWT auth state, computes expiry, and clears the API key', () => {
    useAuthStore.getState().setCredentials('leftover-key', 'tenant-0', 'free');
    vi.useFakeTimers();
    vi.setSystemTime(new Date('2026-01-01T00:00:00Z'));
    useAuthStore.getState().setSSOCredentials('access-tok', 'refresh-tok', 3600, 'tenant-2', 'enterprise');
    const s = useAuthStore.getState();
    expect(s.ssoMode).toBe(true);
    expect(s.accessToken).toBe('access-tok');
    expect(s.refreshToken).toBe('refresh-tok');
    expect(s.tenantId).toBe('tenant-2');
    expect(s.plan).toBe('enterprise');
    expect(s.isAuthenticated).toBe(true);
    expect(s.apiKey).toBe('');
    expect(s.tokenExpiresAt).toBe(Math.floor(new Date('2026-01-01T00:00:00Z').getTime() / 1000) + 3600);
  });

  it('updateAccessToken refreshes the token and expiry while preserving refresh token + SSO mode', () => {
    useAuthStore.getState().setSSOCredentials('old-access', 'refresh-tok', 3600, 'tenant-2', 'enterprise');
    vi.useFakeTimers();
    vi.setSystemTime(new Date('2026-02-01T00:00:00Z'));
    useAuthStore.getState().updateAccessToken('new-access', 7200);
    const s = useAuthStore.getState();
    expect(s.accessToken).toBe('new-access');
    expect(s.refreshToken).toBe('refresh-tok');
    expect(s.ssoMode).toBe(true);
    expect(s.tokenExpiresAt).toBe(Math.floor(new Date('2026-02-01T00:00:00Z').getTime() / 1000) + 7200);
  });

  it('login (legacy) sets API-key credentials with an empty plan and clears SSO fields', () => {
    useAuthStore.getState().setSSOCredentials('a', 'r', 100, 'tenant-x', 'pro');
    useAuthStore.getState().login({ apiKey: 'legacy-key', tenantId: 'legacy-tenant' });
    const s = useAuthStore.getState();
    expect(s.apiKey).toBe('legacy-key');
    expect(s.tenantId).toBe('legacy-tenant');
    expect(s.plan).toBe('');
    expect(s.isAuthenticated).toBe(true);
    expect(s.ssoMode).toBe(false);
    expect(s.accessToken).toBe('');
    expect(s.refreshToken).toBe('');
    expect(s.tokenExpiresAt).toBe(0);
  });

  it('login writes the api key to sessionStorage and clears legacy localStorage copy', () => {
    localStorage.setItem('av_api_key', 'stale-key');
    useAuthStore.getState().login({ apiKey: 'legacy-key', tenantId: 'legacy-tenant' });
    expect(sessionStorage.getItem('av_api_key')).toBe('legacy-key');
    expect(localStorage.getItem('av_api_key')).toBeNull();
  });

  it('logout clears all auth state and both storage locations', () => {
    useAuthStore.getState().setCredentials('key', 'tenant', 'pro');
    useAuthStore.getState().setSessionValidated(true);
    localStorage.setItem('av-auth', 'something');

    useAuthStore.getState().logout();

    const s = useAuthStore.getState();
    expect(s.apiKey).toBe('');
    expect(s.tenantId).toBe('');
    expect(s.plan).toBe('');
    expect(s.isAuthenticated).toBe(false);
    expect(s.ssoMode).toBe(false);
    expect(s.accessToken).toBe('');
    expect(s.refreshToken).toBe('');
    expect(s.tokenExpiresAt).toBe(0);
    expect(s.sessionValidated).toBe(false);
    expect(sessionStorage.getItem('av_api_key')).toBeNull();
    expect(localStorage.getItem('av_api_key')).toBeNull();
    // The persist middleware re-serializes state to sessionStorage on every
    // set() call (including logout's own), so 'av-auth' in sessionStorage is
    // repopulated with the sanitized/logged-out state rather than removed —
    // that's expected. localStorage is never written by secureStorage, so
    // logout's explicit removeItem is what clears it here.
    expect(localStorage.getItem('av-auth')).toBeNull();
  });

  it('setSessionValidated toggles the validated flag', () => {
    useAuthStore.getState().setSessionValidated(true);
    expect(useAuthStore.getState().sessionValidated).toBe(true);
    useAuthStore.getState().setSessionValidated(false);
    expect(useAuthStore.getState().sessionValidated).toBe(false);
  });

  it('setMfaRequired toggles the MFA-required flag', () => {
    useAuthStore.getState().setMfaRequired(true);
    expect(useAuthStore.getState().mfaRequired).toBe(true);
    useAuthStore.getState().setMfaRequired(false);
    expect(useAuthStore.getState().mfaRequired).toBe(false);
  });

  it('setMfaToken sets and clears the pending MFA token', () => {
    useAuthStore.getState().setMfaToken('mfa-tok-1');
    expect(useAuthStore.getState().mfaToken).toBe('mfa-tok-1');
    useAuthStore.getState().setMfaToken(null);
    expect(useAuthStore.getState().mfaToken).toBeNull();
  });
});

describe('getAuthHeader', () => {
  it('returns a Bearer header in SSO mode with an access token', () => {
    useAuthStore.getState().setSSOCredentials('tok-abc', 'refresh', 100, 'tenant-1', 'pro');
    expect(getAuthHeader()).toEqual({ Authorization: 'Bearer tok-abc' });
  });

  it('falls back to X-API-Key when ssoMode is true but there is no access token', () => {
    useAuthStore.setState({ ssoMode: true, accessToken: '', apiKey: 'fallback-key' });
    expect(getAuthHeader()).toEqual({ 'X-API-Key': 'fallback-key' });
  });

  it('returns an X-API-Key header in API-key mode', () => {
    useAuthStore.getState().setCredentials('my-key', 'tenant-1', 'pro');
    expect(getAuthHeader()).toEqual({ 'X-API-Key': 'my-key' });
  });

  it('returns an empty X-API-Key when apiKey is unset', () => {
    expect(getAuthHeader()).toEqual({ 'X-API-Key': '' });
  });
});

describe('isTokenExpired', () => {
  it('returns false outside of SSO mode regardless of tokenExpiresAt', () => {
    useAuthStore.setState({ ssoMode: false, tokenExpiresAt: 1 });
    expect(isTokenExpired()).toBe(false);
  });

  it('returns false when the SSO token has not yet expired', () => {
    vi.useFakeTimers();
    vi.setSystemTime(new Date('2026-01-01T00:00:00Z'));
    const now = Math.floor(Date.now() / 1000);
    useAuthStore.setState({ ssoMode: true, tokenExpiresAt: now + 1000 });
    expect(isTokenExpired()).toBe(false);
  });

  it('returns true when the SSO token has expired', () => {
    vi.useFakeTimers();
    vi.setSystemTime(new Date('2026-01-01T00:00:00Z'));
    const now = Math.floor(Date.now() / 1000);
    useAuthStore.setState({ ssoMode: true, tokenExpiresAt: now - 1000 });
    expect(isTokenExpired()).toBe(true);
  });

  it('treats an unset tokenExpiresAt (0/undefined) as already expired in SSO mode', () => {
    useAuthStore.setState({ ssoMode: true, tokenExpiresAt: 0 });
    expect(isTokenExpired()).toBe(true);
  });
});

describe('secureStorage (sessionStorage-first persistence)', () => {
  it('reads persisted state, preferring sessionStorage over localStorage', async () => {
    const persisted = JSON.stringify({ state: { apiKey: 'from-session' }, version: 0 });
    sessionStorage.setItem('av-auth', persisted);
    localStorage.setItem('av-auth', JSON.stringify({ state: { apiKey: 'from-local' }, version: 0 }));

    await useAuthStore.persist.rehydrate();
    expect(useAuthStore.getState().apiKey).toBe('from-session');
  });

  it('falls back to localStorage when sessionStorage has no persisted state', async () => {
    localStorage.setItem('av-auth', JSON.stringify({ state: { apiKey: 'from-local-only' }, version: 0 }));
    sessionStorage.removeItem('av-auth');

    await useAuthStore.persist.rehydrate();
    expect(useAuthStore.getState().apiKey).toBe('from-local-only');
  });

  it('clearStorage() removes the persisted blob from both storage locations', async () => {
    sessionStorage.setItem('av-auth', JSON.stringify({ state: { apiKey: 'x' }, version: 0 }));
    localStorage.setItem('av-auth', JSON.stringify({ state: { apiKey: 'x' }, version: 0 }));

    await useAuthStore.persist.clearStorage();

    expect(sessionStorage.getItem('av-auth')).toBeNull();
    expect(localStorage.getItem('av-auth')).toBeNull();
  });
});
