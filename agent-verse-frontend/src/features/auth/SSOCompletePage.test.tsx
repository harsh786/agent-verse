import { render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, test, vi } from "vitest";
import { useAuthStore } from "@/stores/auth";
import { SSOCompletePage } from "./SSOCompletePage";

function renderPage(search: string) {
  render(
    <MemoryRouter initialEntries={[`/auth/sso/complete${search}`]}>
      <Routes>
        <Route path="/auth/sso/complete" element={<SSOCompletePage />} />
        <Route path="/auth" element={<div>Auth Page</div>} />
        <Route path="/dashboard" element={<div>Dashboard</div>} />
      </Routes>
    </MemoryRouter>
  );
}

describe("SSOCompletePage", () => {
  beforeEach(() => {
    useAuthStore.getState().logout();
    vi.restoreAllMocks();
  });
  afterEach(() => vi.restoreAllMocks());

  test("exchanges the one-time code for a session token", async () => {
    const fetchMock = vi.spyOn(globalThis, "fetch").mockResolvedValue(
      new Response(
        JSON.stringify({ access_token: "avs_tok", expires_in: 3600, tenant_id: "t1", plan: "starter" }),
        { status: 200 }
      )
    );
    renderPage("?code=abc123");
    expect(await screen.findByText("Dashboard")).toBeInTheDocument();
    const [url, init] = fetchMock.mock.calls[0];
    expect(String(url)).toMatch(/\/auth\/session\/exchange$/);
    expect(init?.method).toBe("POST");
    expect(JSON.parse(String(init?.body))).toEqual({ code: "abc123" });
    const s = useAuthStore.getState();
    expect(s.ssoMode).toBe(true);
    expect(s.accessToken).toBe("avs_tok");
    expect(s.tenantId).toBe("t1");
  });

  test("an expired or used code shows an error and stores nothing", async () => {
    vi.spyOn(globalThis, "fetch").mockResolvedValue(new Response("{}", { status: 401 }));
    renderPage("?code=used");
    expect(await screen.findByRole("alert")).toHaveTextContent(/expired or was already used/i);
    await waitFor(() => expect(useAuthStore.getState().isAuthenticated).toBe(false));
  });

  test("a backend outage is an honest error, not a sign-in", async () => {
    vi.spyOn(globalThis, "fetch").mockResolvedValue(new Response("{}", { status: 503 }));
    renderPage("?code=abc123");
    expect(await screen.findByRole("alert")).toHaveTextContent(/could not be completed/i);
    expect(useAuthStore.getState().isAuthenticated).toBe(false);
    expect(useAuthStore.getState().accessToken).toBe("");
  });

  test("no code is an error", async () => {
    renderPage("");
    expect(await screen.findByRole("alert")).toHaveTextContent(/no login code/i);
  });
});
