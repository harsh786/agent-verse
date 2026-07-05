import { create } from "zustand";
import { persist, createJSONStorage } from "zustand/middleware";

interface AuthState {
  // ── API-key auth (original flow) ─────────────────────────────────────────
  apiKey: string;
  tenantId: string;
  plan: string;
  isAuthenticated: boolean;

  // ── SSO / JWT auth (Keycloak OIDC) ────────────────────────────────────────
  /** True when authenticated via Keycloak SSO rather than a raw API key. */
  ssoMode: boolean;
  /** Keycloak access token (JWT).  Sent as Authorization: Bearer <token>. */
  accessToken: string;
  /** Keycloak refresh token.  Used by useTokenRefresh to obtain new access tokens. */
  refreshToken: string;
  /**
   * Unix epoch (seconds) when the current access token expires.
   * 0 = unknown.  Set from the `expires_in` response field.
   */
  tokenExpiresAt: number;

  // ── Session validation ────────────────────────────────────────────────────
  /**
   * True once the session has been validated against the backend in the current
   * page load.  Prevents RequireAuth from firing GET /tenants/me on every
   * React Router navigation within the same tab.
   */
  sessionValidated: boolean;

  // ── MFA state ─────────────────────────────────────────────────────────────
  /** True when login succeeded but MFA code is still needed. */
  mfaRequired: boolean;
  /** Temporary token identifying the pending MFA session. */
  mfaToken: string | null;

  // ── Actions ───────────────────────────────────────────────────────────────
  /** Set API-key credentials (traditional login). */
  setCredentials: (apiKey: string, tenantId: string, plan: string) => void;
  /** Set JWT credentials after a successful Keycloak SSO callback. */
  setSSOCredentials: (
    accessToken: string,
    refreshToken: string,
    expiresIn: number,
    tenantId: string,
    plan: string
  ) => void;
  /** Update the access token after a silent refresh (preserves refresh token + SSO mode). */
  updateAccessToken: (accessToken: string, expiresIn: number) => void;
  /** Legacy login action kept for backward compatibility. */
  login: (creds: { apiKey: string; tenantId: string }) => void;
  /** Clear all credentials and reset to unauthenticated state. */
  logout: () => void;
  /** Mark the current session as validated so RequireAuth skips future checks. */
  setSessionValidated: (validated: boolean) => void;
  /** Set whether MFA verification is required for the current login attempt. */
  setMfaRequired: (required: boolean) => void;
  /** Set the temporary MFA session token. */
  setMfaToken: (token: string | null) => void;
}

// Use sessionStorage (cleared on tab close) to reduce XSS exfiltration risk.
// Falls back to localStorage for backward compat on reads only — never writes
// API keys or JWTs back to localStorage.
const secureStorage = createJSONStorage(() => ({
  getItem: (name: string): string | null => {
    return sessionStorage.getItem(name) ?? localStorage.getItem(name);
  },
  setItem: (name: string, value: string): void => {
    sessionStorage.setItem(name, value);
    // Do NOT write to localStorage so credentials are not persisted to disk.
  },
  removeItem: (name: string): void => {
    sessionStorage.removeItem(name);
    localStorage.removeItem(name);
  },
}));

export const useAuthStore = create<AuthState>()(
  persist(
    (set) => ({
      // Default state
      apiKey: "",
      tenantId: "",
      plan: "",
      isAuthenticated: false,
      ssoMode: false,
      accessToken: "",
      refreshToken: "",
      tokenExpiresAt: 0,
      sessionValidated: false,
      mfaRequired: false,
      mfaToken: null,

      setCredentials: (apiKey, tenantId, plan) => {
        sessionStorage.setItem("av_api_key", apiKey);
        localStorage.removeItem("av_api_key"); // migration: remove from localStorage
        set({
          apiKey,
          tenantId,
          plan,
          isAuthenticated: true,
          ssoMode: false,
          accessToken: "",
          refreshToken: "",
          tokenExpiresAt: 0,
        });
      },

      setSSOCredentials: (accessToken, refreshToken, expiresIn, tenantId, plan) => {
        set({
          ssoMode: true,
          accessToken,
          refreshToken,
          tokenExpiresAt: Math.floor(Date.now() / 1000) + expiresIn,
          tenantId,
          plan,
          isAuthenticated: true,
          // Clear API-key fields when switching to SSO
          apiKey: "",
        });
      },

      updateAccessToken: (accessToken, expiresIn) => {
        set({
          accessToken,
          tokenExpiresAt: Math.floor(Date.now() / 1000) + expiresIn,
        });
      },

      login: (creds) => {
        sessionStorage.setItem("av_api_key", creds.apiKey);
        localStorage.removeItem("av_api_key"); // migration: remove from localStorage
        set({
          apiKey: creds.apiKey,
          tenantId: creds.tenantId,
          plan: "",
          isAuthenticated: true,
          ssoMode: false,
          accessToken: "",
          refreshToken: "",
          tokenExpiresAt: 0,
        });
      },

      logout: () => {
        sessionStorage.removeItem("av_api_key");
        localStorage.removeItem("av_api_key");
        sessionStorage.removeItem("av-auth");
        localStorage.removeItem("av-auth");
        set({
          apiKey: "",
          tenantId: "",
          plan: "",
          isAuthenticated: false,
          ssoMode: false,
          accessToken: "",
          refreshToken: "",
          tokenExpiresAt: 0,
          sessionValidated: false,
        });
      },

      setSessionValidated: (validated: boolean) => set({ sessionValidated: validated }),
      setMfaRequired: (required) => set({ mfaRequired: required }),
      setMfaToken: (token) => set({ mfaToken: token }),
    }),
    { name: "av-auth", storage: secureStorage }
  )
);

/**
 * Returns the correct auth header for any API call.
 * Handles both API-key mode and SSO Bearer-token mode.
 * Use this in any raw fetch() call instead of building headers manually.
 */
export function getAuthHeader(): Record<string, string> {
  const { ssoMode, accessToken, apiKey } = useAuthStore.getState();
  if (ssoMode && accessToken) {
    return { Authorization: `Bearer ${accessToken}` };
  }
  return { 'X-API-Key': apiKey ?? '' };
}

/** Returns true if the current session token is expired (SSO mode only). */
export function isTokenExpired(): boolean {
  const { ssoMode, tokenExpiresAt } = useAuthStore.getState();
  return ssoMode && Date.now() / 1000 >= (tokenExpiresAt ?? 0);
}
