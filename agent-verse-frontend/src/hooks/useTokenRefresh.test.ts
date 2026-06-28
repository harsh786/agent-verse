import { act, renderHook } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, test, vi } from "vitest";
import { useAuthStore } from "@/stores/auth";
import { useTokenRefresh } from "./useTokenRefresh";

describe("useTokenRefresh", () => {
  beforeEach(() => {
    vi.useFakeTimers();
    vi.spyOn(globalThis, "fetch");
    useAuthStore.setState({
      ssoMode: false,
      accessToken: "",
      refreshToken: "",
      tokenExpiresAt: 0,
      isAuthenticated: false,
      apiKey: "",
      tenantId: "",
      plan: "",
    });
  });

  afterEach(() => {
    vi.useRealTimers();
    vi.restoreAllMocks();
  });

  test("does nothing when not in SSO mode", () => {
    useAuthStore.setState({ ssoMode: false });
    renderHook(() => useTokenRefresh());
    vi.runAllTimers();
    expect(globalThis.fetch).not.toHaveBeenCalled();
  });

  test("does nothing when tokenExpiresAt is 0", () => {
    useAuthStore.setState({
      ssoMode: true,
      refreshToken: "r",
      tokenExpiresAt: 0,
    });
    renderHook(() => useTokenRefresh());
    vi.runAllTimers();
    expect(globalThis.fetch).not.toHaveBeenCalled();
  });

  test("schedules refresh 60 seconds before token expiry", async () => {
    const nowSeconds = Math.floor(Date.now() / 1000);
    useAuthStore.setState({
      ssoMode: true,
      refreshToken: "refresh-token",
      tokenExpiresAt: nowSeconds + 120, // expires in 2 minutes → refresh at T+60
      isAuthenticated: true,
    });

    vi.mocked(globalThis.fetch).mockResolvedValueOnce(
      new Response(
        JSON.stringify({ access_token: "new-access-token", expires_in: 300 }),
        { status: 200, headers: { "Content-Type": "application/json" } }
      )
    );

    renderHook(() => useTokenRefresh());

    // Not yet fired
    await act(async () => {
      vi.advanceTimersByTime(59_000);
    });
    expect(globalThis.fetch).not.toHaveBeenCalled();

    // Now it fires
    await act(async () => {
      vi.advanceTimersByTime(2_000);
    });

    expect(globalThis.fetch).toHaveBeenCalledOnce();
    expect(useAuthStore.getState().accessToken).toBe("new-access-token");
  });

  test("fires immediately when token is already nearly expired", async () => {
    const nowSeconds = Math.floor(Date.now() / 1000);
    useAuthStore.setState({
      ssoMode: true,
      refreshToken: "r",
      tokenExpiresAt: nowSeconds + 10, // only 10 seconds left → already past buffer
      isAuthenticated: true,
    });

    vi.mocked(globalThis.fetch).mockResolvedValueOnce(
      new Response(
        JSON.stringify({ access_token: "new-token", expires_in: 300 }),
        { status: 200, headers: { "Content-Type": "application/json" } }
      )
    );

    renderHook(() => useTokenRefresh());

    await act(async () => {
      vi.runAllTimers();
    });

    expect(globalThis.fetch).toHaveBeenCalled();
  });

  test("logs out on 401 response (refresh token invalid)", async () => {
    const nowSeconds = Math.floor(Date.now() / 1000);
    useAuthStore.setState({
      ssoMode: true,
      refreshToken: "expired-refresh-token",
      tokenExpiresAt: nowSeconds + 10,
      isAuthenticated: true,
    });

    vi.mocked(globalThis.fetch).mockResolvedValueOnce(
      new Response(
        JSON.stringify({ detail: "Refresh token invalid or expired" }),
        {
          status: 401,
          headers: { "Content-Type": "application/json" },
        }
      )
    );

    renderHook(() => useTokenRefresh());

    await act(async () => {
      vi.runAllTimers();
    });

    expect(useAuthStore.getState().isAuthenticated).toBe(false);
  });

  test("logs out on 400 response (refresh token rejected)", async () => {
    const nowSeconds = Math.floor(Date.now() / 1000);
    useAuthStore.setState({
      ssoMode: true,
      refreshToken: "bad-refresh",
      tokenExpiresAt: nowSeconds + 10,
      isAuthenticated: true,
    });

    vi.mocked(globalThis.fetch).mockResolvedValueOnce(
      new Response(JSON.stringify({ detail: "bad_token" }), {
        status: 400,
        headers: { "Content-Type": "application/json" },
      })
    );

    renderHook(() => useTokenRefresh());

    await act(async () => {
      vi.runAllTimers();
    });

    expect(useAuthStore.getState().isAuthenticated).toBe(false);
  });

  test("retries after network error without logging out", async () => {
    const nowSeconds = Math.floor(Date.now() / 1000);
    useAuthStore.setState({
      ssoMode: true,
      refreshToken: "r",
      tokenExpiresAt: nowSeconds + 10,
      isAuthenticated: true,
    });

    // Network error on first attempt
    vi.mocked(globalThis.fetch)
      .mockRejectedValueOnce(new TypeError("fetch failed"))
      .mockResolvedValueOnce(
        new Response(
          JSON.stringify({ access_token: "recovered", expires_in: 300 }),
          { status: 200, headers: { "Content-Type": "application/json" } }
        )
      );

    renderHook(() => useTokenRefresh());

    // First attempt fires immediately
    await act(async () => {
      vi.advanceTimersByTime(1_000);
    });

    // Still authenticated (retry scheduled, not logged out)
    expect(useAuthStore.getState().isAuthenticated).toBe(true);

    // Retry fires after MIN_REFRESH_INTERVAL_MS
    await act(async () => {
      vi.advanceTimersByTime(30_000);
    });

    expect(useAuthStore.getState().accessToken).toBe("recovered");
  });

  test("updates refreshToken when server issues a new one (rolling refresh)", async () => {
    const nowSeconds = Math.floor(Date.now() / 1000);
    useAuthStore.setState({
      ssoMode: true,
      refreshToken: "old-refresh",
      tokenExpiresAt: nowSeconds + 10,
      isAuthenticated: true,
    });

    vi.mocked(globalThis.fetch).mockResolvedValueOnce(
      new Response(
        JSON.stringify({
          access_token: "new-access",
          refresh_token: "new-refresh",
          expires_in: 300,
        }),
        { status: 200, headers: { "Content-Type": "application/json" } }
      )
    );

    renderHook(() => useTokenRefresh());

    await act(async () => {
      vi.runAllTimers();
    });

    expect(useAuthStore.getState().refreshToken).toBe("new-refresh");
    expect(useAuthStore.getState().accessToken).toBe("new-access");
  });

  test("clears timer on unmount", () => {
    const nowSeconds = Math.floor(Date.now() / 1000);
    useAuthStore.setState({
      ssoMode: true,
      refreshToken: "r",
      tokenExpiresAt: nowSeconds + 120,
      isAuthenticated: true,
    });

    const { unmount } = renderHook(() => useTokenRefresh());
    unmount();

    // Advance time past when refresh would have fired
    vi.advanceTimersByTime(65_000);

    // No fetch should have been called after unmount
    expect(globalThis.fetch).not.toHaveBeenCalled();
  });
});
