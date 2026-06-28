import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, test, vi } from "vitest";
import { useAuthStore } from "@/stores/auth";
import { SSOCallbackPage } from "./SSOCallbackPage";

function renderCallback(search: string) {
  render(
    <MemoryRouter initialEntries={[`/auth/callback${search}`]}>
      <Routes>
        <Route path="/auth/callback" element={<SSOCallbackPage />} />
        <Route path="/auth" element={<div>Auth Page</div>} />
        <Route path="/dashboard" element={<div>Dashboard</div>} />
      </Routes>
    </MemoryRouter>
  );
}

describe("SSOCallbackPage", () => {
  beforeEach(() => {
    useAuthStore.setState({
      apiKey: "",
      tenantId: "",
      plan: "",
      isAuthenticated: false,
      ssoMode: false,
      accessToken: "",
      refreshToken: "",
      tokenExpiresAt: 0,
    });
    sessionStorage.clear();
    vi.restoreAllMocks();
  });

  afterEach(() => {
    vi.restoreAllMocks();
  });

  test("shows error when no authorization code in URL", async () => {
    renderCallback("");
    expect(await screen.findByRole("alert")).toBeInTheDocument();
    expect(screen.getByRole("alert")).toHaveTextContent(
      /no authorization code received/i
    );
  });

  test("shows Keycloak error when error param is present", async () => {
    renderCallback(
      "?error=access_denied&error_description=User+denied+access"
    );
    expect(await screen.findByRole("alert")).toBeInTheDocument();
    expect(screen.getByRole("alert")).toHaveTextContent(/User denied access/i);
  });

  test("shows state mismatch error when state does not match stored value", async () => {
    sessionStorage.setItem("av_sso_state", "expected-state");
    renderCallback("?code=abc123&state=wrong-state");
    expect(await screen.findByRole("alert")).toBeInTheDocument();
    expect(screen.getByRole("alert")).toHaveTextContent(
      /security validation failed/i
    );
  });

  test("skips state check when no state was stored (SSO without state param)", async () => {
    // No stored state → skip CSRF check (Keycloak may not send state if not requested)
    vi.spyOn(globalThis, "fetch")
      .mockResolvedValueOnce(
        new Response(
          JSON.stringify({
            access_token: "jwt.access",
            refresh_token: "jwt.refresh",
            expires_in: 300,
            token_type: "Bearer",
          }),
          { status: 200, headers: { "Content-Type": "application/json" } }
        )
      )
      .mockResolvedValueOnce(
        new Response(
          JSON.stringify({
            sub: "u1",
            email: "u@example.com",
            name: "User",
            preferred_username: "user1",
            roles: [],
          }),
          { status: 200, headers: { "Content-Type": "application/json" } }
        )
      );

    renderCallback("?code=valid-code");

    await waitFor(
      () => {
        expect(useAuthStore.getState().isAuthenticated).toBe(true);
      },
      { timeout: 2000 }
    );
  });

  test("exchanges code for tokens and redirects to dashboard on success", async () => {
    vi.spyOn(globalThis, "fetch")
      .mockResolvedValueOnce(
        new Response(
          JSON.stringify({
            access_token: "jwt.access.token",
            refresh_token: "jwt.refresh.token",
            expires_in: 300,
            token_type: "Bearer",
          }),
          { status: 200, headers: { "Content-Type": "application/json" } }
        )
      )
      .mockResolvedValueOnce(
        new Response(
          JSON.stringify({
            sub: "user-123",
            email: "admin@example.com",
            name: "Admin User",
            preferred_username: "admin",
            roles: ["admin"],
          }),
          { status: 200, headers: { "Content-Type": "application/json" } }
        )
      );

    renderCallback("?code=valid-code");

    await waitFor(() => {
      expect(useAuthStore.getState().isAuthenticated).toBe(true);
    });

    expect(useAuthStore.getState().ssoMode).toBe(true);
    expect(useAuthStore.getState().accessToken).toBe("jwt.access.token");
    expect(useAuthStore.getState().refreshToken).toBe("jwt.refresh.token");
    expect(useAuthStore.getState().plan).toBe("enterprise");
    expect(useAuthStore.getState().tenantId).toBe("admin");

    // Wait for the 600ms redirect timer to fire
    expect(await screen.findByText("Dashboard", {}, { timeout: 2000 })).toBeInTheDocument();
  });

  test("maps operator role to professional plan", async () => {
    vi.spyOn(globalThis, "fetch")
      .mockResolvedValueOnce(
        new Response(
          JSON.stringify({
            access_token: "t",
            refresh_token: "r",
            expires_in: 300,
            token_type: "Bearer",
          }),
          { status: 200, headers: { "Content-Type": "application/json" } }
        )
      )
      .mockResolvedValueOnce(
        new Response(
          JSON.stringify({
            sub: "u2",
            email: "op@example.com",
            name: "Operator",
            preferred_username: "op",
            roles: ["operator"],
          }),
          { status: 200, headers: { "Content-Type": "application/json" } }
        )
      );

    renderCallback("?code=op-code");

    await waitFor(() => {
      expect(useAuthStore.getState().isAuthenticated).toBe(true);
    });
    expect(useAuthStore.getState().plan).toBe("professional");
  });

  test("shows error and retry button on token exchange failure", async () => {
    vi.spyOn(globalThis, "fetch").mockResolvedValueOnce(
      new Response(JSON.stringify({ detail: "Code expired" }), {
        status: 400,
        headers: { "Content-Type": "application/json" },
      })
    );

    renderCallback("?code=expired-code");

    expect(await screen.findByRole("alert")).toBeInTheDocument();
    expect(screen.getByRole("alert")).toHaveTextContent(/Code expired/i);
    expect(
      screen.getByRole("button", { name: /back to sign in/i })
    ).toBeInTheDocument();
    expect(useAuthStore.getState().isAuthenticated).toBe(false);
  });

  test("back to sign in button navigates to /auth", async () => {
    vi.spyOn(globalThis, "fetch").mockResolvedValueOnce(
      new Response(JSON.stringify({ detail: "error" }), {
        status: 400,
        headers: { "Content-Type": "application/json" },
      })
    );

    renderCallback("?code=bad-code");

    const retryBtn = await screen.findByRole("button", {
      name: /back to sign in/i,
    });
    await userEvent.click(retryBtn);
    expect(screen.getByText("Auth Page")).toBeInTheDocument();
  });

  test("clears state token from sessionStorage on success", async () => {
    const state = "csrf-state-xyz";
    sessionStorage.setItem("av_sso_state", state);

    vi.spyOn(globalThis, "fetch")
      .mockResolvedValueOnce(
        new Response(
          JSON.stringify({
            access_token: "t",
            refresh_token: "r",
            expires_in: 300,
            token_type: "Bearer",
          }),
          { status: 200, headers: { "Content-Type": "application/json" } }
        )
      )
      .mockResolvedValueOnce(
        new Response(
          JSON.stringify({ sub: "u", email: "u@e.com", name: "U", preferred_username: "u", roles: [] }),
          { status: 200, headers: { "Content-Type": "application/json" } }
        )
      );

    renderCallback(`?code=good&state=${state}`);

    await waitFor(() => {
      expect(useAuthStore.getState().isAuthenticated).toBe(true);
    });

    // State token must be removed to prevent replay
    expect(sessionStorage.getItem("av_sso_state")).toBeNull();
  });
});
