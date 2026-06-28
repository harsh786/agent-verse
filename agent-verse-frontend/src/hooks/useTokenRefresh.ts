/**
 * useTokenRefresh — silent background Keycloak JWT refresh.
 *
 * When the auth store is in SSO mode this hook:
 *   - Schedules a silent token refresh REFRESH_BUFFER_SECONDS before expiry.
 *   - Updates the auth store with the new access token (and refresh token if
 *     the server issued a new one — rolling refresh).
 *   - On a non-recoverable refresh failure (4xx) it logs the user out.
 *   - On a transient network error it retries after MIN_REFRESH_INTERVAL_MS.
 *
 * Mount this hook exactly once, inside AppLayout so it runs for the entire
 * authenticated session.
 */

import { useEffect, useRef } from "react";
import { useAuthStore } from "@/stores/auth";

const API_BASE = (import.meta as any).env?.VITE_API_URL ?? "http://localhost:8000";

/** Refresh this many seconds before the token actually expires. */
const REFRESH_BUFFER_SECONDS = 60;

/**
 * Minimum delay between refresh attempts.
 * Prevents tight retry loops on intermittent network errors.
 */
const MIN_REFRESH_INTERVAL_MS = 30_000;

export function useTokenRefresh(): void {
  const ssoMode = useAuthStore((s) => s.ssoMode);
  const tokenExpiresAt = useAuthStore((s) => s.tokenExpiresAt);

  const timerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const lastAttemptRef = useRef<number>(0);

  useEffect(() => {
    if (!ssoMode || !tokenExpiresAt) {
      clearTimer();
      return;
    }
    scheduleRefresh();
    return () => clearTimer();
  }, [ssoMode, tokenExpiresAt]); // eslint-disable-line react-hooks/exhaustive-deps

  function clearTimer(): void {
    if (timerRef.current !== null) {
      clearTimeout(timerRef.current);
      timerRef.current = null;
    }
  }

  function scheduleRefresh(): void {
    clearTimer();

    const nowSeconds = Math.floor(Date.now() / 1000);
    const secondsUntilRefresh =
      tokenExpiresAt - nowSeconds - REFRESH_BUFFER_SECONDS;

    // Respect minimum interval to avoid hammering the token endpoint on failure
    const timeSinceLastAttemptMs = Date.now() - lastAttemptRef.current;
    const throttleRemainingMs = Math.max(
      0,
      MIN_REFRESH_INTERVAL_MS - timeSinceLastAttemptMs
    );

    const delayMs = Math.max(
      secondsUntilRefresh * 1000,
      throttleRemainingMs
    );

    timerRef.current = setTimeout(() => {
      void performRefresh();
    }, Math.max(0, delayMs));
  }

  async function performRefresh(): Promise<void> {
    lastAttemptRef.current = Date.now();

    // Always read fresh state to avoid stale closures
    const { refreshToken, logout, updateAccessToken } = useAuthStore.getState();

    if (!refreshToken) {
      logout();
      return;
    }

    try {
      const res = await fetch(
        `${API_BASE}/auth/refresh?refresh_token_value=${encodeURIComponent(refreshToken)}`,
        { method: "POST" }
      );

      if (!res.ok) {
        // 401/400 → refresh token is invalid or expired; force logout
        logout();
        return;
      }

      const data = (await res.json()) as {
        access_token: string;
        refresh_token?: string;
        expires_in: number;
      };

      updateAccessToken(data.access_token, data.expires_in);

      // Rolling refresh: update refresh token if server issued a new one
      if (data.refresh_token && data.refresh_token !== refreshToken) {
        useAuthStore.setState({ refreshToken: data.refresh_token });
      }

      // The next refresh is scheduled by the useEffect re-running because
      // tokenExpiresAt changed (via updateAccessToken).
    } catch {
      // Transient network error — retry after minimum interval
      timerRef.current = setTimeout(() => {
        void performRefresh();
      }, MIN_REFRESH_INTERVAL_MS);
    }
  }
}
