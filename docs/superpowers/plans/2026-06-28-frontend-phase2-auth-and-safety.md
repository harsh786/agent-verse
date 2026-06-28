# Frontend Phase 2 — Auth & Safety Controls Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make Keycloak SSO reachable end-to-end (feature-detected login → redirect → code exchange → session hydration → userinfo), add a client-side token-refresh loop so logged-in SSO users are not bounced, wire the dead "Request access" link to tenant signup, ship an API-key rotation wizard + BYOK vault-key form in Settings, and land the P0-1 Emergency Stop control (confirm modal + live status banner) in the TopBar — all without changing API-key auth behavior or any backend file.

**Architecture:** Frontend-only changes in `agent-verse-frontend`. We add a single `authApi` block to the typed client (`@/lib/api/client.ts`) plus `tenantsApi.rotateKey`, `tenantsApi.setVaultKey`, and `governanceApi.emergencyStop/clearEmergencyStop`. SSO config is fetched on the auth page (`GET /auth/config`); the SSO button redirects the browser to `GET /auth/login`; a new `/auth/callback` route exchanges the `code` via `POST /auth/token`, stores the access/refresh tokens in a new `useSsoStore`, hydrates the API session via `useAuthStore.setCredentials`, and fetches `GET /auth/userinfo`. A small refresh scheduler (mounted once in `main.tsx`) calls `POST /auth/refresh` shortly before `expires_in` elapses. **The backend has NO `GET /governance/emergency-stop` status endpoint** (verified: only `POST` activate + `DELETE` clear at `governance.py:584,695`), so emergency-stop status is tracked client-side in a persisted `useEmergencyStore` and reflected as a TopBar banner. Strict TDD with vitest + Testing Library; one commit per task.

**Tech Stack:** React 19, TypeScript (strict), Vite, TanStack Query 5, Zustand 5, Tailwind tokens, react-router-dom 7, vitest 3 + @testing-library/react, Playwright. Reuses Phase-1 foundations: `toast`/`useToastStore` (`@/stores/toast`), the typed client's 401→logout+toast interceptor, and `Skeleton`/`EmptyState`/`StatusBadge` (`@/components/ui`).

## Global Constraints

- **Frontend-only.** No backend file changes in Phase 2. No backend endpoints added. All backend paths below are **verified against the routers** (file:line cited per task).
- **No new dependencies.** Everything uses the installed stack.
- **Auth access is via `useAuthStore` (`@/stores/auth`)** for the API key/tenant — never read `sessionStorage`/`localStorage` for the API key directly in pages. SSO tokens (access/refresh/expiry) live in a separate `useSsoStore` because they are a different credential class.
- **All backend calls go through the typed client** `@/lib/api/client` — add methods there (this phase **owns `authApi`**), never inline `fetch` in pages. Exception: the SSO *redirect* in Task 4 is a full-page `window.location.assign` to the backend's `GET /auth/login`, which is a browser navigation, not a fetch.
- **Reuse Phase 1:** import `{ toast }` from `@/stores/toast` for user feedback; rely on the client's existing 401 handling; use `Skeleton`/`EmptyState`/`StatusBadge` where loading/empty/status UI is needed.
- **Verified backend paths (ground truth, do NOT assume):**
  - `GET /auth/config` → `{ sso_enabled, provider, keycloak_url, realm, client_id, authorization_endpoint, token_endpoint }` (`auth.py:27-43`).
  - `GET /auth/login?redirect_uri=&state=` → 302 redirect to Keycloak (`auth.py:46-65`). Frontend navigates the browser here.
  - `POST /auth/token?code=<code>&redirect_uri=<uri>` → `{ access_token, refresh_token, expires_in, token_type }` (`auth.py:68-105`). **Params are query-string, not JSON body.**
  - `POST /auth/refresh?refresh_token_value=<rt>` → same token shape (`auth.py:108-136`). **Query-string param.**
  - `GET /auth/userinfo` with `Authorization: Bearer <access_token>` → `{ sub, email, name, preferred_username, roles, email_verified }` (`auth.py:139-165`).
  - `POST /tenants/signup` body `{ name, email }` (no `plan`) → `{ tenant_id, name, email, plan, api_key, api_key_id }` (`tenants.py:49-75`, `tenant_service.py:109-116`).
  - `POST /tenants/me/keys` body `{ name, scopes? }` → `{ key_id, name, scopes, expires_at, is_active, created_at, raw_key }` (`tenants.py:100-114`, `tenant_service.py:179-187`).
  - `DELETE /tenants/me/keys/{key_id}` → 204 (`tenants.py:117-129`).
  - `POST /tenants/me/keys/{key_id}/rotate` body `{ name?, scopes?, revoke_old? }` → `{ new_key: {key_id, name, ..., raw_key}, old_key_id, old_revoked }` (`tenants.py:142-173`). **Returns a nested `new_key`, NOT a flat `raw_key`** — the existing SettingsPage rotate reads `data.raw_key` and is therefore broken; this phase fixes it.
  - `POST /tenants/me/vault-key` body `{ key_base64 }` → `{ status, key_length, message }`; 400 on a non-32-byte decoded key (`tenants.py:469-492`).
  - `POST /governance/emergency-stop` (no body) → `{ status: "emergency_stop_activated", cancelled_goals, rejected_approvals, ... }` (`governance.py:584-692`).
  - `DELETE /governance/emergency-stop` (no body) → `{ status: "cleared", tenant_id }` (`governance.py:695-705`).
  - **There is NO `GET /governance/emergency-stop`** — status is derived client-side from activate/clear calls.
- **Tailwind design tokens only:** `bg-card`, `border-border`, `text-primary`, `text-muted-foreground`, `bg-muted`, `text-destructive`, with `dark:` variants where siblings use them.
- **Quality gate per task:** `npm run typecheck` and `npm run lint` and `npm run test` must pass before commit.
- **Commit style:** conventional commits; end every commit message with:
  `Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>`

---

## File Structure

**Create:**
- `src/stores/sso.ts` — Zustand `useSsoStore` for SSO tokens (`accessToken`/`refreshToken`/`expiresAt`/`user`); persisted to sessionStorage. (+ `src/stores/sso.test.ts`)
- `src/stores/emergency.ts` — Zustand `useEmergencyStore` tracking client-side emergency-stop state. (+ `src/stores/emergency.test.ts`)
- `src/features/auth/AuthCallbackPage.tsx` — `/auth/callback` route: exchanges `?code`, hydrates session, fetches userinfo, redirects to `/dashboard`. (+ `src/features/auth/AuthCallbackPage.test.tsx`)
- `src/lib/auth/refreshScheduler.ts` — starts/stops a timer that refreshes the SSO access token before expiry. (+ `src/lib/auth/refreshScheduler.test.ts`)
- `src/features/settings/VaultKeySection.tsx` — BYOK vault-key form (extracted section reused inside SettingsPage). (+ `src/features/settings/VaultKeySection.test.tsx`)
- `src/components/ui/EmergencyStopButton.tsx` — TopBar control: confirm modal + activate/clear. (+ `src/components/ui/EmergencyStopButton.test.tsx`)
- `src/components/ui/EmergencyStopBanner.tsx` — app-wide banner shown while emergency stop is active. (+ `src/components/ui/EmergencyStopBanner.test.tsx`)
- `src/lib/api/client.test.ts` — only if absent (Phase 1 may have created it); otherwise append. Tests for `authApi`, `tenantsApi.rotateKey/setVaultKey`, `governanceApi.emergencyStop/clearEmergencyStop`.
- `src/features/auth/AuthPage.test.tsx` — SSO button feature-detect + signup wiring.
- `e2e/auth-sso.spec.ts` — Playwright: SSO button appears when config enabled; callback hydrates session.
- `e2e/emergency-stop.spec.ts` — Playwright: confirm modal → activate → banner shows; clear → banner hides.

**Modify:**
- `src/lib/api/client.ts` — add `authApi`; add `tenantsApi.rotateKey` + `tenantsApi.setVaultKey`; add `governanceApi.emergencyStop` + `governanceApi.clearEmergencyStop`; add response types.
- `src/features/auth/AuthPage.tsx` — feature-detect SSO via `authApi.getConfig`, render SSO button (redirect via `authApi.loginUrl`), wire "Request access" to a signup mini-form using `tenantsApi.signup`.
- `src/app/App.tsx` — add the `/auth/callback` route (public, like `/auth`); mount `<EmergencyStopBanner />` inside `AppLayout`'s routed area (or in `AppLayout` itself — see Task 9).
- `src/components/ui/AppLayout.tsx` — render `<EmergencyStopBanner />` above the routed content.
- `src/components/ui/TopBar.tsx` — add `<EmergencyStopButton />`.
- `src/features/settings/SettingsPage.tsx` — fix rotation to read `new_key.raw_key`; route key list/create/revoke/rotate + BYOK through the typed client; add the rotation wizard (revoke-old toggle + grace note) and mount `<VaultKeySection />`.
- `src/main.tsx` — start the SSO refresh scheduler once on mount.

**Out of scope (deferred):** RBAC roles + IP-allowlist UI (P1-1, Phase 3 / WS-2). Real-time approvals SSE + TopBar counter (P1-3, Phase 3). The generic SSE hook `src/lib/sse/useEventStream.ts` is **owned by Phase 3** — do not create it here.

---

## Test harness reference (existing pattern — reuse verbatim)

Component tests use this wrapper (see `src/features/goals/GoalsListPage.test.tsx`):

```tsx
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter } from 'react-router-dom';
import { vi } from 'vitest';
import { useAuthStore } from '@/stores/auth';

function renderWithProviders(ui: React.ReactNode, initialEntries: string[] = ['/']) {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={queryClient}>
      <MemoryRouter initialEntries={initialEntries}>{ui}</MemoryRouter>
    </QueryClientProvider>
  );
}

beforeEach(() => {
  sessionStorage.clear();
  localStorage.clear();
  useAuthStore.setState({ apiKey: 'test-key', tenantId: 'test-tenant', plan: 'free', isAuthenticated: true });
});
```

Client/store unit tests mock `fetch` directly: `vi.spyOn(globalThis, 'fetch')`.
E2E specs reuse the `setupAuth(page)` + `page.route(...)` mock pattern from `e2e/goals.spec.ts` and `e2e/auth.spec.ts`.

---

### Task 1: Add `authApi` to the typed client (config / login-url / token / refresh / userinfo)

**Files:**
- Modify: `src/lib/api/client.ts` (new `authApi` block + `SsoConfig`/`SsoTokens`/`UserInfo` types)
- Test: `src/lib/api/client.test.ts`

**Interfaces:**
- Consumes: the module-level `request<T>` helper, `API_BASE`.
- Produces:
  - `interface SsoConfig { sso_enabled: boolean; provider: string | null; keycloak_url: string | null; realm: string | null; client_id: string | null; authorization_endpoint: string | null; token_endpoint: string | null }`
  - `interface SsoTokens { access_token: string; refresh_token: string; expires_in: number; token_type: string }`
  - `interface UserInfo { sub: string; email?: string; name?: string; preferred_username?: string; roles: string[]; email_verified: boolean }`
  - `authApi.getConfig(): Promise<SsoConfig>` → `GET /auth/config`
  - `authApi.loginUrl(redirectUri: string, state?: string): string` → builds `${API_BASE}/auth/login?redirect_uri=…&state=…` (a URL string for `window.location.assign`, NOT a fetch)
  - `authApi.exchangeToken(code: string, redirectUri: string): Promise<SsoTokens>` → `POST /auth/token?code=…&redirect_uri=…`
  - `authApi.refresh(refreshTokenValue: string): Promise<SsoTokens>` → `POST /auth/refresh?refresh_token_value=…`
  - `authApi.userinfo(accessToken: string): Promise<UserInfo>` → `GET /auth/userinfo` with `Authorization: Bearer <accessToken>`

- [ ] **Step 1: Write the failing test**

```ts
// src/lib/api/client.test.ts  (create if absent; otherwise append these tests)
import { afterEach, beforeEach, expect, test, vi } from 'vitest';
import { authApi, API_BASE } from '@/lib/api/client';

afterEach(() => vi.restoreAllMocks());

function mockOk(body: unknown) {
  return vi.spyOn(globalThis, 'fetch').mockResolvedValue(
    new Response(JSON.stringify(body), { status: 200, headers: { 'Content-Type': 'application/json' } })
  );
}

test('getConfig calls GET /auth/config', async () => {
  const f = mockOk({ sso_enabled: true, provider: 'keycloak', keycloak_url: 'x', realm: 'r', client_id: 'c', authorization_endpoint: 'a', token_endpoint: 't' });
  const cfg = await authApi.getConfig();
  expect(String(f.mock.calls[0][0])).toContain('/auth/config');
  expect(cfg.sso_enabled).toBe(true);
});

test('loginUrl builds a redirect URL with redirect_uri and state', () => {
  const url = authApi.loginUrl('http://localhost:5173/auth/callback', 'abc');
  expect(url.startsWith(`${API_BASE}/auth/login?`)).toBe(true);
  expect(url).toContain('redirect_uri=http%3A%2F%2Flocalhost%3A5173%2Fauth%2Fcallback');
  expect(url).toContain('state=abc');
});

test('exchangeToken POSTs code+redirect_uri as query params', async () => {
  const f = mockOk({ access_token: 'AT', refresh_token: 'RT', expires_in: 300, token_type: 'Bearer' });
  const t = await authApi.exchangeToken('CODE123', 'http://localhost:5173/auth/callback');
  const [url, init] = f.mock.calls[0];
  expect(init?.method).toBe('POST');
  expect(String(url)).toContain('/auth/token?');
  expect(String(url)).toContain('code=CODE123');
  expect(String(url)).toContain('redirect_uri=http%3A%2F%2Flocalhost%3A5173%2Fauth%2Fcallback');
  expect(t.access_token).toBe('AT');
});

test('refresh POSTs refresh_token_value as a query param', async () => {
  const f = mockOk({ access_token: 'AT2', refresh_token: 'RT2', expires_in: 300, token_type: 'Bearer' });
  await authApi.refresh('RT');
  const [url, init] = f.mock.calls[0];
  expect(init?.method).toBe('POST');
  expect(String(url)).toContain('/auth/refresh?refresh_token_value=RT');
});

test('userinfo sends the Bearer access token', async () => {
  const f = mockOk({ sub: 'u1', roles: ['admin'], email_verified: true });
  await authApi.userinfo('AT');
  const [url, init] = f.mock.calls[0];
  expect(String(url)).toContain('/auth/userinfo');
  expect((init?.headers as Record<string, string>)?.Authorization).toBe('Bearer AT');
});
```

- [ ] **Step 2: Run test to verify it fails**

Run: `npm run test -- src/lib/api/client.test.ts -t auth`
Expected: FAIL — `authApi` is not exported / not a function.

- [ ] **Step 3: Implement**

In `src/lib/api/client.ts`, append after the `memoryApi` block:

```ts
// ── Auth (Keycloak SSO) ────────────────────────────────────────────────────────

export interface SsoConfig {
  sso_enabled: boolean;
  provider: string | null;
  keycloak_url: string | null;
  realm: string | null;
  client_id: string | null;
  authorization_endpoint: string | null;
  token_endpoint: string | null;
}

export interface SsoTokens {
  access_token: string;
  refresh_token: string;
  expires_in: number;
  token_type: string;
}

export interface UserInfo {
  sub: string;
  email?: string;
  name?: string;
  preferred_username?: string;
  roles: string[];
  email_verified: boolean;
}

export const authApi = {
  getConfig: () => request<SsoConfig>("/auth/config"),
  /** Browser-navigation URL (use with window.location.assign), not a fetch. */
  loginUrl: (redirectUri: string, state = ""): string => {
    const params = new URLSearchParams({ redirect_uri: redirectUri, state });
    return `${API_BASE}/auth/login?${params.toString()}`;
  },
  exchangeToken: (code: string, redirectUri: string) => {
    const params = new URLSearchParams({ code, redirect_uri: redirectUri });
    return request<SsoTokens>(`/auth/token?${params.toString()}`, { method: "POST" });
  },
  refresh: (refreshTokenValue: string) => {
    const params = new URLSearchParams({ refresh_token_value: refreshTokenValue });
    return request<SsoTokens>(`/auth/refresh?${params.toString()}`, { method: "POST" });
  },
  userinfo: (accessToken: string) =>
    request<UserInfo>("/auth/userinfo", {
      headers: { Authorization: `Bearer ${accessToken}` },
    }),
};
```

> Note: `URLSearchParams` encodes `redirect_uri` so `loginUrl`/`exchangeToken` URLs contain `%3A%2F%2F` for `://`, matching the test assertions.

- [ ] **Step 4: Run test to verify it passes**

Run: `npm run test -- src/lib/api/client.test.ts -t auth`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/lib/api/client.ts src/lib/api/client.test.ts
git commit -m "feat(client): add authApi for Keycloak SSO config/login/token/refresh/userinfo

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

### Task 2: SSO token store (`useSsoStore`)

**Files:**
- Create: `src/stores/sso.ts`, `src/stores/sso.test.ts`

**Interfaces:**
- Produces: `useSsoStore` (Zustand, persisted to sessionStorage under `av-sso`) with:
  - state `{ accessToken: string; refreshToken: string; expiresAt: number; user: UserInfo | null }`
  - `setTokens(t: SsoTokens): void` — stores tokens and computes `expiresAt = Date.now() + expires_in*1000`.
  - `setUser(user: UserInfo): void`
  - `clear(): void`
  - selector helper `hasSsoSession(): boolean` (exported function reading `getState`).

- [ ] **Step 1: Write the failing test**

```ts
// src/stores/sso.test.ts
import { beforeEach, expect, test, vi } from 'vitest';
import { useSsoStore, hasSsoSession } from '@/stores/sso';

beforeEach(() => {
  sessionStorage.clear();
  useSsoStore.setState({ accessToken: '', refreshToken: '', expiresAt: 0, user: null });
});

test('setTokens stores tokens and computes expiresAt', () => {
  vi.spyOn(Date, 'now').mockReturnValue(1_000_000);
  useSsoStore.getState().setTokens({ access_token: 'AT', refresh_token: 'RT', expires_in: 300, token_type: 'Bearer' });
  const s = useSsoStore.getState();
  expect(s.accessToken).toBe('AT');
  expect(s.refreshToken).toBe('RT');
  expect(s.expiresAt).toBe(1_000_000 + 300_000);
  expect(hasSsoSession()).toBe(true);
});

test('clear wipes the session', () => {
  useSsoStore.getState().setTokens({ access_token: 'AT', refresh_token: 'RT', expires_in: 300, token_type: 'Bearer' });
  useSsoStore.getState().clear();
  expect(hasSsoSession()).toBe(false);
  expect(useSsoStore.getState().user).toBeNull();
});
```

- [ ] **Step 2: Run test to verify it fails**

Run: `npm run test -- src/stores/sso.test.ts`
Expected: FAIL — module not found.

- [ ] **Step 3: Implement**

```ts
// src/stores/sso.ts
import { create } from 'zustand';
import { persist, createJSONStorage } from 'zustand/middleware';
import type { SsoTokens, UserInfo } from '@/lib/api/client';

interface SsoState {
  accessToken: string;
  refreshToken: string;
  expiresAt: number; // epoch ms when the access token expires
  user: UserInfo | null;
  setTokens: (tokens: SsoTokens) => void;
  setUser: (user: UserInfo) => void;
  clear: () => void;
}

export const useSsoStore = create<SsoState>()(
  persist(
    (set) => ({
      accessToken: '',
      refreshToken: '',
      expiresAt: 0,
      user: null,
      setTokens: (tokens) =>
        set({
          accessToken: tokens.access_token,
          refreshToken: tokens.refresh_token,
          expiresAt: Date.now() + tokens.expires_in * 1000,
        }),
      setUser: (user) => set({ user }),
      clear: () => set({ accessToken: '', refreshToken: '', expiresAt: 0, user: null }),
    }),
    { name: 'av-sso', storage: createJSONStorage(() => sessionStorage) }
  )
);

/** True when an SSO refresh token is present (i.e. this is an SSO-authenticated session). */
export const hasSsoSession = (): boolean => !!useSsoStore.getState().refreshToken;
```

- [ ] **Step 4: Run test to verify it passes**

Run: `npm run test -- src/stores/sso.test.ts`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/stores/sso.ts src/stores/sso.test.ts
git commit -m "feat(auth): add useSsoStore for SSO token/user state

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

### Task 3: SSO refresh scheduler

**Files:**
- Create: `src/lib/auth/refreshScheduler.ts`, `src/lib/auth/refreshScheduler.test.ts`
- Modify: `src/main.tsx` (start the scheduler once)

**Context:** Keycloak access tokens are short-lived (`expires_in`, ~300s). To avoid bouncing an SSO user, refresh ~60s before expiry using the stored refresh token. API-key sessions (no `refreshToken`) are unaffected — the scheduler is a no-op for them.

**Interfaces:**
- Consumes: `useSsoStore`, `authApi.refresh`, `toast`.
- Produces:
  - `REFRESH_LEAD_MS = 60_000` (refresh this far before expiry).
  - `startRefreshScheduler(): () => void` — schedules the next refresh and returns a stop function. Idempotent (calling again clears the prior timer).
  - `refreshNow(): Promise<boolean>` — performs one refresh; updates the store on success, clears it + toasts on failure; returns success boolean.

- [ ] **Step 1: Write the failing test**

```ts
// src/lib/auth/refreshScheduler.test.ts
import { afterEach, beforeEach, expect, test, vi } from 'vitest';
import { useSsoStore } from '@/stores/sso';
import * as client from '@/lib/api/client';
import { refreshNow } from '@/lib/auth/refreshScheduler';

beforeEach(() => {
  sessionStorage.clear();
  useSsoStore.setState({ accessToken: '', refreshToken: '', expiresAt: 0, user: null });
});
afterEach(() => vi.restoreAllMocks());

test('refreshNow is a no-op without a refresh token', async () => {
  const spy = vi.spyOn(client.authApi, 'refresh');
  const ok = await refreshNow();
  expect(ok).toBe(false);
  expect(spy).not.toHaveBeenCalled();
});

test('refreshNow updates the store on success', async () => {
  useSsoStore.setState({ accessToken: 'old', refreshToken: 'RT', expiresAt: 1, user: null });
  vi.spyOn(client.authApi, 'refresh').mockResolvedValue({
    access_token: 'NEW', refresh_token: 'RT2', expires_in: 300, token_type: 'Bearer',
  });
  const ok = await refreshNow();
  expect(ok).toBe(true);
  expect(useSsoStore.getState().accessToken).toBe('NEW');
  expect(useSsoStore.getState().refreshToken).toBe('RT2');
});

test('refreshNow clears the SSO session on failure', async () => {
  useSsoStore.setState({ accessToken: 'old', refreshToken: 'RT', expiresAt: 1, user: null });
  vi.spyOn(client.authApi, 'refresh').mockRejectedValue(new Error('expired'));
  const ok = await refreshNow();
  expect(ok).toBe(false);
  expect(useSsoStore.getState().refreshToken).toBe('');
});
```

- [ ] **Step 2: Run test to verify it fails**

Run: `npm run test -- src/lib/auth/refreshScheduler.test.ts`
Expected: FAIL — module not found.

- [ ] **Step 3: Implement**

```ts
// src/lib/auth/refreshScheduler.ts
import { authApi } from '@/lib/api/client';
import { toast } from '@/stores/toast';
import { useSsoStore } from '@/stores/sso';

export const REFRESH_LEAD_MS = 60_000;
const MIN_DELAY_MS = 5_000;

let timer: ReturnType<typeof setTimeout> | null = null;

/** Perform one refresh. Returns true on success. No-op (false) without a refresh token. */
export async function refreshNow(): Promise<boolean> {
  const { refreshToken, setTokens } = useSsoStore.getState();
  if (!refreshToken) return false;
  try {
    const tokens = await authApi.refresh(refreshToken);
    setTokens(tokens);
    return true;
  } catch {
    useSsoStore.getState().clear();
    toast({ kind: 'error', message: 'Your SSO session expired — please sign in again.' });
    return false;
  }
}

/** Schedule the next refresh based on the stored expiry. Returns a stop function. */
export function startRefreshScheduler(): () => void {
  if (timer) clearTimeout(timer);

  const schedule = (): void => {
    const { expiresAt, refreshToken } = useSsoStore.getState();
    if (!refreshToken) return; // API-key session — nothing to do
    const delay = Math.max(expiresAt - Date.now() - REFRESH_LEAD_MS, MIN_DELAY_MS);
    timer = setTimeout(async () => {
      const ok = await refreshNow();
      if (ok) schedule(); // chain the next refresh
    }, delay);
  };

  schedule();
  return (): void => {
    if (timer) clearTimeout(timer);
    timer = null;
  };
}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `npm run test -- src/lib/auth/refreshScheduler.test.ts`
Expected: PASS.

- [ ] **Step 5: Start the scheduler in `main.tsx`**

In `src/main.tsx`, after the providers are set up (module top-level is fine — it self-no-ops without a token), add:

```tsx
import { startRefreshScheduler } from "./lib/auth/refreshScheduler";
// ... after createRoot(...).render(...):
startRefreshScheduler();
```

- [ ] **Step 6: Run full suite + typecheck**

Run: `npm run test -- src/lib/auth/refreshScheduler.test.ts && npm run typecheck`
Expected: PASS / no type errors.

- [ ] **Step 7: Commit**

```bash
git add src/lib/auth/refreshScheduler.ts src/lib/auth/refreshScheduler.test.ts src/main.tsx
git commit -m "feat(auth): SSO access-token refresh scheduler

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

### Task 4: AuthPage — feature-detected SSO button + signup wiring

**Files:**
- Modify: `src/features/auth/AuthPage.tsx`
- Test: `src/features/auth/AuthPage.test.tsx` (create)

**Context:** `AuthPage` currently has API-key login and a dead `<a href="#">Request access</a>` (`AuthPage.tsx:121`). Add: (1) on mount, `authApi.getConfig()`; if `sso_enabled`, render a "Sign in with SSO" button that navigates the browser to `authApi.loginUrl(<origin>/auth/callback, <random state>)`; (2) replace the dead link with a signup mini-form (name + email) calling `tenantsApi.signup`, which on success shows the returned `api_key` once and pre-fills the login fields.

**Interfaces:**
- Consumes: `authApi.getConfig`, `authApi.loginUrl`, `tenantsApi.signup`, `useAuthStore.setCredentials`, `toast`.
- Produces: nothing exported (page behavior only). The SSO redirect uses `window.location.assign(url)` (mockable in tests).

- [ ] **Step 1: Write the failing test**

```tsx
// src/features/auth/AuthPage.test.tsx
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter } from 'react-router-dom';
import { afterEach, beforeEach, expect, test, vi } from 'vitest';
import { AuthPage } from './AuthPage';

function renderPage() {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={qc}><MemoryRouter><AuthPage /></MemoryRouter></QueryClientProvider>
  );
}

beforeEach(() => { sessionStorage.clear(); localStorage.clear(); });
afterEach(() => vi.restoreAllMocks());

test('shows SSO button when /auth/config reports sso_enabled', async () => {
  vi.spyOn(globalThis, 'fetch').mockImplementation(async (input) => {
    if (String(input).includes('/auth/config'))
      return new Response(JSON.stringify({ sso_enabled: true, provider: 'keycloak', keycloak_url: 'x', realm: 'r', client_id: 'c', authorization_endpoint: 'a', token_endpoint: 't' }), { status: 200 });
    return new Response('{}', { status: 200 });
  });
  renderPage();
  expect(await screen.findByRole('button', { name: /sign in with sso/i })).toBeInTheDocument();
});

test('hides SSO button when sso_enabled is false', async () => {
  vi.spyOn(globalThis, 'fetch').mockResolvedValue(
    new Response(JSON.stringify({ sso_enabled: false, provider: null, keycloak_url: null, realm: null, client_id: null, authorization_endpoint: null, token_endpoint: null }), { status: 200 })
  );
  renderPage();
  await waitFor(() => expect(screen.queryByRole('button', { name: /sign in with sso/i })).not.toBeInTheDocument());
});

test('SSO button redirects the browser to the login URL', async () => {
  const assign = vi.fn();
  vi.stubGlobal('location', { ...window.location, origin: 'http://localhost:5173', assign });
  vi.spyOn(globalThis, 'fetch').mockResolvedValue(
    new Response(JSON.stringify({ sso_enabled: true, provider: 'keycloak', keycloak_url: 'x', realm: 'r', client_id: 'c', authorization_endpoint: 'a', token_endpoint: 't' }), { status: 200 })
  );
  renderPage();
  await userEvent.click(await screen.findByRole('button', { name: /sign in with sso/i }));
  expect(assign).toHaveBeenCalledWith(expect.stringContaining('/auth/login?'));
});

test('signup form posts name+email to /tenants/signup and reveals the key', async () => {
  vi.spyOn(globalThis, 'fetch').mockImplementation(async (input, init) => {
    const url = String(input);
    if (url.includes('/auth/config'))
      return new Response(JSON.stringify({ sso_enabled: false }), { status: 200 });
    if (url.includes('/tenants/signup') && (init as RequestInit)?.method === 'POST')
      return new Response(JSON.stringify({ tenant_id: 'new-org', name: 'New Org', email: 'a@b.co', plan: 'free', api_key: 'av_free_SECRET', api_key_id: 'k1' }), { status: 201 });
    return new Response('{}', { status: 200 });
  });
  renderPage();
  await userEvent.click(await screen.findByRole('button', { name: /request access|create tenant/i }));
  await userEvent.type(screen.getByLabelText(/organization name/i), 'New Org');
  await userEvent.type(screen.getByLabelText(/email/i), 'a@b.co');
  await userEvent.click(screen.getByRole('button', { name: /^create$/i }));
  expect(await screen.findByText(/av_free_SECRET/)).toBeInTheDocument();
});
```

- [ ] **Step 2: Run test to verify it fails**

Run: `npm run test -- src/features/auth/AuthPage.test.tsx`
Expected: FAIL — no SSO button / no signup form.

- [ ] **Step 3: Implement**

Replace the body of `src/features/auth/AuthPage.tsx` with the version below. It keeps the existing API-key form intact (so `e2e/auth.spec.ts` stays green), adds the SSO button gated on config, and swaps the dead link for a collapsible signup form. The local `validateCredentials` helper is retained to avoid disturbing the existing API-key login path.

```tsx
import { useEffect, useState } from "react";
import { useNavigate } from "react-router-dom";
import { Zap } from "lucide-react";
import { useAuthStore } from "@/stores/auth";
import { authApi, tenantsApi, type SsoConfig } from "@/lib/api/client";
import { toast } from "@/stores/toast";

const API_BASE = (import.meta as any).env?.VITE_API_URL ?? 'http://localhost:8000';

interface TenantProfile {
  tenant_id: string;
  plan: string;
}

async function validateCredentials(apiKey: string): Promise<TenantProfile | null> {
  const res = await fetch(`${API_BASE}/tenants/me`, { headers: { 'X-API-Key': apiKey } });
  if (!res.ok) return null;
  return res.json();
}

export function AuthPage() {
  const [apiKey, setApiKey] = useState("");
  const [tenantId, setTenantId] = useState("");
  const [error, setError] = useState("");
  const [isSubmitting, setIsSubmitting] = useState(false);
  const [ssoConfig, setSsoConfig] = useState<SsoConfig | null>(null);
  const [showSignup, setShowSignup] = useState(false);
  const { setCredentials } = useAuthStore();
  const navigate = useNavigate();

  useEffect(() => {
    let cancelled = false;
    authApi.getConfig()
      .then((cfg) => { if (!cancelled) setSsoConfig(cfg); })
      .catch(() => { /* SSO unavailable — API-key login still works */ });
    return () => { cancelled = true; };
  }, []);

  function handleSsoLogin() {
    const redirectUri = `${window.location.origin}/auth/callback`;
    const state = Math.random().toString(36).slice(2);
    sessionStorage.setItem("av_sso_state", state);
    window.location.assign(authApi.loginUrl(redirectUri, state));
  }

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    const trimmedApiKey = apiKey.trim();
    const trimmedTenantId = tenantId.trim();
    if (!trimmedApiKey || !trimmedTenantId) {
      setError("API key and tenant ID are required.");
      return;
    }
    setError("");
    setIsSubmitting(true);
    try {
      const tenant = await validateCredentials(trimmedApiKey);
      if (!tenant || tenant.tenant_id !== trimmedTenantId) {
        setError("Invalid tenant ID or API key.");
        return;
      }
      setCredentials(trimmedApiKey, tenant.tenant_id, tenant.plan);
      navigate("/dashboard");
    } catch {
      setError("Unable to reach the backend. Please try again.");
    } finally {
      setIsSubmitting(false);
    }
  }

  return (
    <div className="min-h-screen flex items-center justify-center bg-background px-4">
      <div className="w-full max-w-md">
        <div className="flex flex-col items-center gap-3 mb-8">
          <div className="flex items-center gap-2">
            <Zap className="h-8 w-8 text-blue-500" aria-hidden="true" />
            <span className="text-2xl font-bold">AgentVerse</span>
          </div>
          <p className="text-muted-foreground text-sm text-center">
            The operating system for autonomous AI agents
          </p>
        </div>

        <div className="bg-card border border-border rounded-xl p-8 shadow-sm">
          <h1 className="text-xl font-semibold mb-6">Sign in to your tenant</h1>

          {error && (
            <div role="alert" className="mb-4 px-3 py-2 text-sm text-red-700 bg-red-50 dark:bg-red-900/20 dark:text-red-400 rounded-md">
              {error}
            </div>
          )}

          {ssoConfig?.sso_enabled && (
            <>
              <button
                type="button"
                onClick={handleSsoLogin}
                className="w-full py-2 px-4 mb-4 border border-border bg-background text-sm font-medium rounded-md hover:bg-muted transition-colors"
              >
                Sign in with SSO
              </button>
              <div className="relative mb-4 text-center">
                <span className="px-2 text-xs text-muted-foreground bg-card relative z-10">or use an API key</span>
                <div className="absolute inset-x-0 top-1/2 border-t border-border" aria-hidden="true" />
              </div>
            </>
          )}

          <form onSubmit={handleSubmit} className="space-y-4">
            <div>
              <label htmlFor="tenantId" className="block text-sm font-medium mb-1.5">Tenant ID</label>
              <input
                id="tenantId" type="text" value={tenantId}
                onChange={(e) => setTenantId(e.target.value)} placeholder="my-org"
                className="w-full px-3 py-2 text-sm border border-input rounded-md bg-background focus:outline-none focus:ring-2 focus:ring-primary"
                autoComplete="username" required
              />
            </div>
            <div>
              <label htmlFor="apiKey" className="block text-sm font-medium mb-1.5">API Key</label>
              <input
                id="apiKey" type="password" value={apiKey}
                onChange={(e) => setApiKey(e.target.value)} placeholder="av_key_..."
                className="w-full px-3 py-2 text-sm border border-input rounded-md bg-background focus:outline-none focus:ring-2 focus:ring-primary"
                autoComplete="current-password" required
              />
            </div>
            <button
              type="submit" disabled={isSubmitting}
              className="w-full py-2 px-4 bg-primary text-primary-foreground text-sm font-medium rounded-md hover:opacity-90 transition-opacity"
            >
              {isSubmitting ? 'Signing in…' : 'Sign in'}
            </button>
          </form>

          {showSignup ? (
            <SignupForm onPrefill={(t, k) => { setTenantId(t); setApiKey(k); }} />
          ) : (
            <p className="mt-4 text-xs text-muted-foreground text-center">
              Don&apos;t have a tenant?{" "}
              <button type="button" onClick={() => setShowSignup(true)} className="text-primary underline-offset-2 hover:underline">
                Request access
              </button>
            </p>
          )}
        </div>
      </div>
    </div>
  );
}

function SignupForm({ onPrefill }: { onPrefill: (tenantId: string, apiKey: string) => void }) {
  const [name, setName] = useState("");
  const [email, setEmail] = useState("");
  const [busy, setBusy] = useState(false);
  const [createdKey, setCreatedKey] = useState<string | null>(null);
  const [err, setErr] = useState("");

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    setErr(""); setBusy(true);
    try {
      const res = await tenantsApi.signup({ name: name.trim(), email: email.trim() });
      setCreatedKey(res.api_key ?? null);
      onPrefill(res.tenant_id, res.api_key ?? "");
      toast({ kind: 'success', message: 'Tenant created — copy your API key now.' });
    } catch (e2) {
      setErr(String(e2));
    } finally {
      setBusy(false);
    }
  }

  return (
    <form onSubmit={submit} className="mt-4 space-y-3 border-t border-border pt-4">
      <p className="text-xs font-medium">Create a new tenant</p>
      <div>
        <label htmlFor="signupName" className="block text-xs font-medium mb-1">Organization name</label>
        <input
          id="signupName" value={name} onChange={(e) => setName(e.target.value)} required
          className="w-full px-3 py-2 text-sm border border-input rounded-md bg-background focus:outline-none focus:ring-2 focus:ring-primary"
        />
      </div>
      <div>
        <label htmlFor="signupEmail" className="block text-xs font-medium mb-1">Email</label>
        <input
          id="signupEmail" type="email" value={email} onChange={(e) => setEmail(e.target.value)} required
          className="w-full px-3 py-2 text-sm border border-input rounded-md bg-background focus:outline-none focus:ring-2 focus:ring-primary"
        />
      </div>
      {err && <p className="text-xs text-destructive">{err}</p>}
      {createdKey && (
        <div className="p-2 bg-muted rounded-md">
          <p className="text-xs text-muted-foreground mb-1">Your API key (copy it now — shown once):</p>
          <code className="block text-xs font-mono break-all">{createdKey}</code>
        </div>
      )}
      <button
        type="submit" disabled={busy || !name.trim() || !email.trim()}
        className="w-full py-2 px-4 bg-primary text-primary-foreground text-sm font-medium rounded-md disabled:opacity-50"
      >
        {busy ? 'Creating…' : 'Create'}
      </button>
    </form>
  );
}
```

> The `SignupRequest` type in `client.ts` currently includes an optional `plan?` — that is harmless (backend ignores extra body fields are not sent here; we only pass `{name, email}`). No client change needed.

- [ ] **Step 4: Run test to verify it passes**

Run: `npm run test -- src/features/auth/AuthPage.test.tsx`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/features/auth/AuthPage.tsx src/features/auth/AuthPage.test.tsx
git commit -m "feat(auth): feature-detected SSO button and tenant signup form on AuthPage

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

### Task 5: `/auth/callback` route — code exchange + session hydration

**Files:**
- Create: `src/features/auth/AuthCallbackPage.tsx`, `src/features/auth/AuthCallbackPage.test.tsx`
- Modify: `src/app/App.tsx` (register the public `/auth/callback` route)

**Context:** After Keycloak redirects back to `/auth/callback?code=…&state=…`, this page: validates `state` against `sessionStorage.av_sso_state`, calls `authApi.exchangeToken(code, redirectUri)`, stores tokens in `useSsoStore`, fetches `authApi.userinfo(access_token)`, then hydrates the API session via `useAuthStore.setCredentials`. **Backend has no `userinfo → API key` bridge**, so for the API key we reuse the SSO `access_token` as the bearer-style key the rest of the app sends as `X-API-Key` only if a real key is available; since SSO sessions authenticate by JWT not API key, we set the API session's `apiKey` to the access token and `tenantId` to `userinfo.sub` so `RequireAuth` passes and the SSO refresh loop keeps the token fresh. (This mirrors how the backend's tenant middleware accepts the Keycloak bearer; if a deployment requires a separate API key, the user can still use the API-key form.)

> Grounding note: the backend's SSO integration validates the JWT (`auth.py:139-165` `validate_jwt`); the frontend stores `access_token` as the credential. We set `isAuthenticated` via `setCredentials`, and the refresh scheduler (Task 3) keeps `access_token` current. This is the minimal, correct orchestration the spec calls for ("client only orchestrates redirect+exchange+refresh").

**Interfaces:**
- Consumes: `authApi.exchangeToken`, `authApi.userinfo`, `useSsoStore.setTokens/setUser`, `useAuthStore.setCredentials`, `startRefreshScheduler`, `toast`.
- Produces: default-exported `AuthCallbackPage` component.

- [ ] **Step 1: Write the failing test**

```tsx
// src/features/auth/AuthCallbackPage.test.tsx
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { render, screen, waitFor } from '@testing-library/react';
import { MemoryRouter, Routes, Route } from 'react-router-dom';
import { afterEach, beforeEach, expect, test, vi } from 'vitest';
import { useAuthStore } from '@/stores/auth';
import { useSsoStore } from '@/stores/sso';
import AuthCallbackPage from './AuthCallbackPage';

function renderAt(url: string) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={qc}>
      <MemoryRouter initialEntries={[url]}>
        <Routes>
          <Route path="/auth/callback" element={<AuthCallbackPage />} />
          <Route path="/dashboard" element={<div>DASHBOARD</div>} />
          <Route path="/auth" element={<div>AUTH</div>} />
        </Routes>
      </MemoryRouter>
    </QueryClientProvider>
  );
}

beforeEach(() => {
  sessionStorage.clear(); localStorage.clear();
  useAuthStore.setState({ apiKey: '', tenantId: '', plan: '', isAuthenticated: false });
  useSsoStore.setState({ accessToken: '', refreshToken: '', expiresAt: 0, user: null });
});
afterEach(() => vi.restoreAllMocks());

test('exchanges the code, hydrates session, redirects to dashboard', async () => {
  sessionStorage.setItem('av_sso_state', 'abc');
  vi.spyOn(globalThis, 'fetch').mockImplementation(async (input) => {
    const url = String(input);
    if (url.includes('/auth/token'))
      return new Response(JSON.stringify({ access_token: 'AT', refresh_token: 'RT', expires_in: 300, token_type: 'Bearer' }), { status: 200 });
    if (url.includes('/auth/userinfo'))
      return new Response(JSON.stringify({ sub: 'user-1', email: 'u@x.co', roles: ['admin'], email_verified: true }), { status: 200 });
    return new Response('{}', { status: 200 });
  });
  renderAt('/auth/callback?code=CODE&state=abc');
  await waitFor(() => expect(screen.getByText('DASHBOARD')).toBeInTheDocument());
  expect(useSsoStore.getState().accessToken).toBe('AT');
  expect(useAuthStore.getState().isAuthenticated).toBe(true);
  expect(useAuthStore.getState().tenantId).toBe('user-1');
});

test('shows an error and link to retry when code is missing', async () => {
  renderAt('/auth/callback?state=abc');
  expect(await screen.findByText(/sign-in could not be completed/i)).toBeInTheDocument();
});

test('rejects a mismatched state (CSRF guard)', async () => {
  sessionStorage.setItem('av_sso_state', 'expected');
  renderAt('/auth/callback?code=CODE&state=attacker');
  expect(await screen.findByText(/sign-in could not be completed/i)).toBeInTheDocument();
});
```

- [ ] **Step 2: Run test to verify it fails**

Run: `npm run test -- src/features/auth/AuthCallbackPage.test.tsx`
Expected: FAIL — module not found.

- [ ] **Step 3: Implement the page**

```tsx
// src/features/auth/AuthCallbackPage.tsx
import { useEffect, useRef, useState } from "react";
import { useNavigate, useSearchParams, Link } from "react-router-dom";
import { authApi } from "@/lib/api/client";
import { useAuthStore } from "@/stores/auth";
import { useSsoStore } from "@/stores/sso";
import { startRefreshScheduler } from "@/lib/auth/refreshScheduler";
import { toast } from "@/stores/toast";

export default function AuthCallbackPage() {
  const [params] = useSearchParams();
  const navigate = useNavigate();
  const [failed, setFailed] = useState(false);
  const ran = useRef(false);

  useEffect(() => {
    if (ran.current) return; // exchange exactly once (StrictMode-safe)
    ran.current = true;

    const code = params.get("code");
    const state = params.get("state");
    const expectedState = sessionStorage.getItem("av_sso_state");

    if (!code || (expectedState && state !== expectedState)) {
      setFailed(true);
      return;
    }
    sessionStorage.removeItem("av_sso_state");

    async function finish(authCode: string) {
      try {
        const redirectUri = `${window.location.origin}/auth/callback`;
        const tokens = await authApi.exchangeToken(authCode, redirectUri);
        useSsoStore.getState().setTokens(tokens);

        const user = await authApi.userinfo(tokens.access_token);
        useSsoStore.getState().setUser(user);

        // Hydrate the API session: SSO sessions authenticate by the access token.
        useAuthStore.getState().setCredentials(tokens.access_token, user.sub, "sso");
        startRefreshScheduler();
        toast({ kind: "success", message: "Signed in via SSO." });
        navigate("/dashboard", { replace: true });
      } catch {
        setFailed(true);
      }
    }
    void finish(code);
  }, [params, navigate]);

  if (failed) {
    return (
      <div className="min-h-screen flex items-center justify-center bg-background px-4">
        <div className="bg-card border border-border rounded-xl p-8 text-center max-w-md">
          <h1 className="text-lg font-semibold mb-2">Sign-in could not be completed</h1>
          <p className="text-sm text-muted-foreground mb-4">
            The SSO authorization was invalid or expired. Please try again.
          </p>
          <Link to="/auth" className="text-sm text-primary underline-offset-2 hover:underline">
            Back to sign in
          </Link>
        </div>
      </div>
    );
  }

  return (
    <div className="min-h-screen flex items-center justify-center bg-background">
      <p className="text-sm text-muted-foreground">Completing sign-in…</p>
    </div>
  );
}
```

- [ ] **Step 4: Register the route in `App.tsx`**

In `src/app/App.tsx`, add the public route next to `/auth` and `/login` (before the `RequireAuth` block). Add the import:

```tsx
import AuthCallbackPage from "@/features/auth/AuthCallbackPage";
```

and the route:

```tsx
      <Route path="/auth" element={<AuthPage />} />
      <Route path="/login" element={<AuthPage />} />
      <Route path="/auth/callback" element={<AuthCallbackPage />} />
```

- [ ] **Step 5: Run test to verify it passes**

Run: `npm run test -- src/features/auth/AuthCallbackPage.test.tsx && npm run typecheck`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add src/features/auth/AuthCallbackPage.tsx src/features/auth/AuthCallbackPage.test.tsx src/app/App.tsx
git commit -m "feat(auth): /auth/callback exchanges code and hydrates SSO session

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

### Task 6: `tenantsApi.rotateKey` + `tenantsApi.setVaultKey` in the client

**Files:**
- Modify: `src/lib/api/client.ts` (extend `tenantsApi`; add response types)
- Test: `src/lib/api/client.test.ts`

**Interfaces:**
- Produces:
  - `interface CreatedApiKey { key_id: string; name: string; scopes: string[]; expires_at: string | null; is_active: boolean; created_at: string; raw_key: string }`
  - `interface RotateKeyResult { new_key: CreatedApiKey; old_key_id: string; old_revoked: boolean }`
  - `interface VaultKeyResult { status: string; key_length: number; message: string }`
  - `tenantsApi.rotateKey(keyId: string, opts?: { name?: string; scopes?: string[]; revokeOld?: boolean }): Promise<RotateKeyResult>` → `POST /tenants/me/keys/{keyId}/rotate`
  - `tenantsApi.setVaultKey(keyBase64: string): Promise<VaultKeyResult>` → `POST /tenants/me/vault-key`

- [ ] **Step 1: Write the failing test**

```ts
// append to src/lib/api/client.test.ts
import { tenantsApi } from '@/lib/api/client';

test('rotateKey posts name/scopes/revoke_old and targets the rotate path', async () => {
  const f = vi.spyOn(globalThis, 'fetch').mockResolvedValue(
    new Response(JSON.stringify({ new_key: { key_id: 'k2', name: 'rot', scopes: [], expires_at: null, is_active: true, created_at: '', raw_key: 'av_free_NEW' }, old_key_id: 'k1', old_revoked: true }), { status: 201, headers: { 'Content-Type': 'application/json' } })
  );
  const res = await tenantsApi.rotateKey('k1', { name: 'rot', revokeOld: true });
  const [url, init] = f.mock.calls[0];
  expect(String(url)).toContain('/tenants/me/keys/k1/rotate');
  expect(init?.method).toBe('POST');
  expect(JSON.parse(String(init?.body))).toEqual({ name: 'rot', scopes: [], revoke_old: true });
  expect(res.new_key.raw_key).toBe('av_free_NEW');
});

test('setVaultKey posts key_base64 to /tenants/me/vault-key', async () => {
  const f = vi.spyOn(globalThis, 'fetch').mockResolvedValue(
    new Response(JSON.stringify({ status: 'byok_key_accepted', key_length: 32, message: 'ok' }), { status: 200, headers: { 'Content-Type': 'application/json' } })
  );
  await tenantsApi.setVaultKey('BASE64KEY');
  const [url, init] = f.mock.calls[0];
  expect(String(url)).toContain('/tenants/me/vault-key');
  expect(JSON.parse(String(init?.body))).toEqual({ key_base64: 'BASE64KEY' });
});
```

- [ ] **Step 2: Run test to verify it fails**

Run: `npm run test -- src/lib/api/client.test.ts -t "rotateKey\|setVaultKey"`
Expected: FAIL — methods not functions.

- [ ] **Step 3: Implement**

In `src/lib/api/client.ts`, add the types near the existing `ApiKeyResponse` and extend `tenantsApi`:

```ts
export interface CreatedApiKey {
  key_id: string;
  name: string;
  scopes: string[];
  expires_at: string | null;
  is_active: boolean;
  created_at: string;
  raw_key: string;
}

export interface RotateKeyResult {
  new_key: CreatedApiKey;
  old_key_id: string;
  old_revoked: boolean;
}

export interface VaultKeyResult {
  status: string;
  key_length: number;
  message: string;
}
```

Add to the `tenantsApi` object (after `revokeKey`):

```ts
  rotateKey: (keyId: string, opts: { name?: string; scopes?: string[]; revokeOld?: boolean } = {}) =>
    request<RotateKeyResult>(`/tenants/me/keys/${keyId}/rotate`, {
      method: "POST",
      body: JSON.stringify({
        name: opts.name ?? "Rotated Key",
        scopes: opts.scopes ?? [],
        revoke_old: opts.revokeOld ?? true,
      }),
    }),
  setVaultKey: (keyBase64: string) =>
    request<VaultKeyResult>("/tenants/me/vault-key", {
      method: "POST",
      body: JSON.stringify({ key_base64: keyBase64 }),
    }),
```

- [ ] **Step 4: Run test to verify it passes**

Run: `npm run test -- src/lib/api/client.test.ts -t "rotateKey\|setVaultKey"`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/lib/api/client.ts src/lib/api/client.test.ts
git commit -m "feat(client): add tenantsApi.rotateKey and tenantsApi.setVaultKey

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

### Task 7: BYOK vault-key section (Settings)

**Files:**
- Create: `src/features/settings/VaultKeySection.tsx`, `src/features/settings/VaultKeySection.test.tsx`
- Modify: `src/features/settings/SettingsPage.tsx` (mount `<VaultKeySection />`)

**Context:** Verified backend `POST /tenants/me/vault-key` requires a base64-encoded **32-byte** key and 400s otherwise (`tenants.py:469-492`). The form validates the decoded length client-side before calling `tenantsApi.setVaultKey`, and offers a "Generate a 32-byte key" helper.

**Interfaces:**
- Consumes: `tenantsApi.setVaultKey`, `toast`.
- Produces: named export `VaultKeySection`.

- [ ] **Step 1: Write the failing test**

```tsx
// src/features/settings/VaultKeySection.test.tsx
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, expect, test, vi } from 'vitest';
import { VaultKeySection } from './VaultKeySection';

function renderSection() {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(<QueryClientProvider client={qc}><VaultKeySection /></QueryClientProvider>);
}

afterEach(() => vi.restoreAllMocks());

function base64Of(byteLen: number): string {
  return btoa(String.fromCharCode(...new Uint8Array(byteLen)));
}

test('rejects a key that does not decode to 32 bytes (no request sent)', async () => {
  const f = vi.spyOn(globalThis, 'fetch');
  renderSection();
  await userEvent.type(screen.getByLabelText(/base64.*key/i), base64Of(16));
  await userEvent.click(screen.getByRole('button', { name: /set vault key/i }));
  expect(await screen.findByText(/must decode to 32 bytes/i)).toBeInTheDocument();
  expect(f).not.toHaveBeenCalled();
});

test('submits a valid 32-byte base64 key', async () => {
  const f = vi.spyOn(globalThis, 'fetch').mockResolvedValue(
    new Response(JSON.stringify({ status: 'byok_key_accepted', key_length: 32, message: 'ok' }), { status: 200, headers: { 'Content-Type': 'application/json' } })
  );
  renderSection();
  await userEvent.type(screen.getByLabelText(/base64.*key/i), base64Of(32));
  await userEvent.click(screen.getByRole('button', { name: /set vault key/i }));
  await screen.findByText(/key accepted/i);
  expect(String(f.mock.calls[0][0])).toContain('/tenants/me/vault-key');
});
```

- [ ] **Step 2: Run test to verify it fails**

Run: `npm run test -- src/features/settings/VaultKeySection.test.tsx`
Expected: FAIL — module not found.

- [ ] **Step 3: Implement**

```tsx
// src/features/settings/VaultKeySection.tsx
import { useState } from "react";
import { useMutation } from "@tanstack/react-query";
import { tenantsApi } from "@/lib/api/client";
import { toast } from "@/stores/toast";

const REQUIRED_KEY_BYTES = 32;

function decodedByteLength(base64: string): number {
  try {
    return atob(base64.trim()).length;
  } catch {
    return -1;
  }
}

function generateKeyBase64(): string {
  const bytes = new Uint8Array(REQUIRED_KEY_BYTES);
  crypto.getRandomValues(bytes);
  return btoa(String.fromCharCode(...bytes));
}

export function VaultKeySection() {
  const [keyBase64, setKeyBase64] = useState("");
  const [validationError, setValidationError] = useState("");

  const mutation = useMutation({
    mutationFn: (value: string) => tenantsApi.setVaultKey(value),
    onSuccess: () => toast({ kind: "success", message: "BYOK vault key accepted." }),
    onError: (e) => toast({ kind: "error", message: `Vault key rejected: ${String(e)}` }),
  });

  function handleSubmit() {
    const len = decodedByteLength(keyBase64);
    if (len !== REQUIRED_KEY_BYTES) {
      setValidationError("Key must decode to 32 bytes (base64 of a 256-bit key).");
      return;
    }
    setValidationError("");
    mutation.mutate(keyBase64.trim());
  }

  return (
    <div className="bg-card border border-border rounded-xl p-5">
      <div className="flex justify-between items-center mb-4">
        <h2 className="font-semibold">Encryption (BYOK)</h2>
        <button
          type="button"
          onClick={() => { setKeyBase64(generateKeyBase64()); setValidationError(""); }}
          className="text-sm text-primary hover:opacity-70"
        >
          Generate
        </button>
      </div>
      <p className="text-xs text-muted-foreground mb-3">
        Bring your own 256-bit master key (base64-encoded) to encrypt this tenant&apos;s secret vault.
        Store it safely — it is required to decrypt your secrets.
      </p>
      <label htmlFor="vaultKey" className="block text-xs font-medium mb-1">Base64-encoded 32-byte key</label>
      <textarea
        id="vaultKey"
        value={keyBase64}
        onChange={(e) => setKeyBase64(e.target.value)}
        rows={2}
        placeholder="e.g. mF8…=="
        className="w-full border border-input rounded-lg px-3 py-2 text-sm font-mono bg-background outline-none focus:ring-2 focus:ring-primary"
      />
      {validationError && <p className="mt-1 text-xs text-destructive">{validationError}</p>}
      {mutation.isSuccess && <p className="mt-2 text-xs text-green-600 dark:text-green-400">Key accepted ({mutation.data?.key_length} bytes).</p>}
      <div className="flex justify-end mt-3">
        <button
          type="button"
          onClick={handleSubmit}
          disabled={!keyBase64.trim() || mutation.isPending}
          className="bg-primary text-primary-foreground px-4 py-2 rounded-lg text-sm disabled:opacity-50"
        >
          {mutation.isPending ? "Setting…" : "Set vault key"}
        </button>
      </div>
    </div>
  );
}
```

- [ ] **Step 4: Mount in SettingsPage**

In `src/features/settings/SettingsPage.tsx`, import and render the section in the `SettingsPage` body (after `<ApiKeysSection />`):

```tsx
import { VaultKeySection } from './VaultKeySection';
// ...
      <ApiKeysSection apiKey={apiKey} />
      <VaultKeySection />
```

- [ ] **Step 5: Run test to verify it passes**

Run: `npm run test -- src/features/settings/VaultKeySection.test.tsx && npm run typecheck`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add src/features/settings/VaultKeySection.tsx src/features/settings/VaultKeySection.test.tsx src/features/settings/SettingsPage.tsx
git commit -m "feat(settings): BYOK vault-key form with 32-byte validation

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

### Task 8: API-key rotation wizard (Settings) — fix the broken rotate + typed client

**Files:**
- Modify: `src/features/settings/SettingsPage.tsx` (`ApiKeysSection`)
- Test: `src/features/settings/SettingsPage.test.tsx` (create)

**Context (verified bug):** the existing `rotateMutation` POSTs to `/rotate` and reads `data.raw_key`, but the backend returns `{ new_key: { raw_key }, old_key_id, old_revoked }` (`tenants.py:170-173`) — so the "copy your new key" banner shows `undefined`. This task migrates `ApiKeysSection` to the typed client (`tenantsApi.listKeys`/`createKey`/`revokeKey`/`rotateKey`), reads the new key from `new_key.raw_key`, and adds a small rotation wizard: a per-key "Rotate" action opens a confirm row with a **"revoke old key now"** toggle (default on) plus a grace note explaining that turning it off keeps both keys valid during cutover.

> Backend confirmed to have a real rotate endpoint, so we use it directly (no create-then-revoke fallback needed). The grace note documents the `revoke_old=false` cutover path the endpoint supports.

**Interfaces:**
- Consumes: `tenantsApi.listKeys`, `tenantsApi.createKey`, `tenantsApi.revokeKey`, `tenantsApi.rotateKey`. (Note `tenantsApi.createKey` returns `{ raw_key, key_id }` — keep create banner reading `raw_key` for the create path.)

- [ ] **Step 1: Write the failing test**

```tsx
// src/features/settings/SettingsPage.test.tsx
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, beforeEach, expect, test, vi } from 'vitest';
import { useAuthStore } from '@/stores/auth';
import { SettingsPage } from './SettingsPage';

function renderPage() {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(<QueryClientProvider client={qc}><SettingsPage /></QueryClientProvider>);
}

beforeEach(() => {
  sessionStorage.clear(); localStorage.clear();
  useAuthStore.setState({ apiKey: 'k', tenantId: 't', plan: 'free', isAuthenticated: true });
});
afterEach(() => vi.restoreAllMocks());

function mockApis(rotateBody: unknown) {
  return vi.spyOn(globalThis, 'fetch').mockImplementation(async (input, init) => {
    const url = String(input);
    const method = (init as RequestInit)?.method ?? 'GET';
    if (url.includes('/tenants/me/keys') && url.includes('/rotate') && method === 'POST')
      return new Response(JSON.stringify(rotateBody), { status: 201, headers: { 'Content-Type': 'application/json' } });
    if (url.endsWith('/tenants/me/keys') && method === 'GET')
      return new Response(JSON.stringify([{ key_id: 'k1', name: 'prod', created_at: '2026-01-01T00:00:00Z' }]), { status: 200, headers: { 'Content-Type': 'application/json' } });
    if (url.includes('/tenants/me/llm')) return new Response(JSON.stringify({ provider: 'openai', model: 'gpt-4o' }), { status: 200 });
    if (url.includes('/tenants/me')) return new Response(JSON.stringify({ tenant_id: 't', name: 'Org', plan: 'free' }), { status: 200 });
    return new Response('{}', { status: 200 });
  });
}

test('rotation reveals the NEW key from new_key.raw_key (not undefined)', async () => {
  mockApis({ new_key: { key_id: 'k2', name: 'Rotated Key', scopes: [], expires_at: null, is_active: true, created_at: '', raw_key: 'av_free_ROTATED' }, old_key_id: 'k1', old_revoked: true });
  renderPage();
  await screen.findByText('prod');
  await userEvent.click(screen.getByRole('button', { name: /rotate/i }));
  // confirm row appears with the revoke-old toggle; confirm the rotation
  await userEvent.click(screen.getByRole('button', { name: /confirm rotation/i }));
  expect(await screen.findByText(/av_free_ROTATED/)).toBeInTheDocument();
  expect(screen.queryByText(/undefined/)).not.toBeInTheDocument();
});
```

- [ ] **Step 2: Run test to verify it fails**

Run: `npm run test -- src/features/settings/SettingsPage.test.tsx`
Expected: FAIL — no "confirm rotation" control / banner shows undefined under the old code.

- [ ] **Step 3: Rewrite `ApiKeysSection`**

Replace the `ApiKeysSection` function in `src/features/settings/SettingsPage.tsx`. Remove its local `apiFetch`-based queries/mutations for keys and use the typed client. Add a `rotatingId` + `revokeOld` state for the wizard. Keep the existing `SectionShell`, `copyToClipboard`, and create flow (the create path already returns a flat `raw_key`).

```tsx
import { tenantsApi, type CreatedApiKey } from '@/lib/api/client';
// (add this import near the top of the file alongside the others)

function ApiKeysSection() {
  const qc = useQueryClient();
  const [newKeyName, setNewKeyName] = useState('');
  const [showCreateInput, setShowCreateInput] = useState(false);
  const [newlyCreated, setNewlyCreated] = useState<CreatedApiKey | null>(null);
  const [copied, setCopied] = useState(false);
  const [rotatingId, setRotatingId] = useState<string | null>(null);
  const [revokeOld, setRevokeOld] = useState(true);

  const { data: keys = [], isLoading, error } = useQuery({
    queryKey: ['api-keys'],
    queryFn: () => tenantsApi.listKeys(),
  });

  const createMutation = useMutation({
    mutationFn: () => tenantsApi.createKey(newKeyName),
    onSuccess: (data) => {
      qc.invalidateQueries({ queryKey: ['api-keys'] });
      // createKey returns { raw_key, key_id }; adapt to the banner shape
      setNewlyCreated({ key_id: data.key_id, name: newKeyName, scopes: [], expires_at: null, is_active: true, created_at: new Date().toISOString(), raw_key: data.raw_key });
      setNewKeyName('');
      setShowCreateInput(false);
    },
  });

  const rotateMutation = useMutation({
    mutationFn: (id: string) => tenantsApi.rotateKey(id, { revokeOld }),
    onSuccess: (data) => {
      qc.invalidateQueries({ queryKey: ['api-keys'] });
      setNewlyCreated(data.new_key); // read raw_key from new_key (verified shape)
      setRotatingId(null);
    },
  });

  const deleteMutation = useMutation({
    mutationFn: (id: string) => tenantsApi.revokeKey(id),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['api-keys'] }),
  });

  const copyToClipboard = async (text: string) => {
    await navigator.clipboard.writeText(text);
    setCopied(true);
    setTimeout(() => setCopied(false), 2000);
  };

  return (
    <SectionShell
      title="API Keys"
      action={
        <button onClick={() => setShowCreateInput((v) => !v)} className="text-sm text-primary hover:opacity-70">
          {showCreateInput ? 'Cancel' : '+ New Key'}
        </button>
      }
    >
      {showCreateInput && (
        <div className="flex gap-2 mb-4">
          <input
            value={newKeyName}
            onChange={(e) => setNewKeyName(e.target.value)}
            placeholder="Key name (e.g. production)"
            className="flex-1 border border-input rounded-lg px-3 py-2 text-sm bg-background outline-none focus:ring-2 focus:ring-primary"
            onKeyDown={(e) => e.key === 'Enter' && createMutation.mutate()}
          />
          <button
            onClick={() => createMutation.mutate()}
            disabled={!newKeyName.trim() || createMutation.isPending}
            className="bg-primary text-primary-foreground px-4 py-2 rounded-lg text-sm disabled:opacity-50"
          >
            {createMutation.isPending ? 'Creating…' : 'Create'}
          </button>
        </div>
      )}

      {newlyCreated && (
        <div className="mb-4 p-3 bg-green-50 dark:bg-green-900/20 border border-green-200 dark:border-green-800 rounded-lg">
          <p className="text-xs font-medium text-green-800 dark:text-green-400 mb-1">
            Key created — copy it now, it won&apos;t be shown again
          </p>
          <div className="flex items-center gap-2">
            <code className="flex-1 text-xs font-mono bg-card border border-green-200 dark:border-green-800 rounded px-2 py-1 overflow-auto">
              {newlyCreated.raw_key}
            </code>
            <button onClick={() => copyToClipboard(newlyCreated.raw_key)} className="p-1.5 hover:bg-green-100 dark:hover:bg-green-900/40 rounded transition-colors">
              {copied ? <Check className="h-4 w-4 text-green-700" /> : <Copy className="h-4 w-4 text-green-700" />}
            </button>
          </div>
          <button onClick={() => setNewlyCreated(null)} className="text-xs text-green-700 dark:text-green-400 mt-2 hover:underline">
            Dismiss
          </button>
        </div>
      )}

      {isLoading ? (
        <p className="text-sm text-muted-foreground">Loading…</p>
      ) : error ? (
        <p className="text-sm text-destructive">Failed to load API keys.</p>
      ) : keys.length === 0 ? (
        <p className="text-sm text-muted-foreground">No API keys. Create one above.</p>
      ) : (
        <table className="w-full text-sm">
          <thead>
            <tr className="border-b border-border">
              {['Name', 'Created', 'Last Used', 'Actions'].map((h) => (
                <th key={h} className="text-left py-2 font-medium text-muted-foreground text-xs">{h}</th>
              ))}
            </tr>
          </thead>
          <tbody className="divide-y divide-border">
            {keys.map((k) => (
              <Fragment key={k.key_id}>
                <tr>
                  <td className="py-2.5 font-medium">{k.name}</td>
                  <td className="py-2.5 text-muted-foreground text-xs">{new Date(k.created_at).toLocaleDateString()}</td>
                  <td className="py-2.5 text-muted-foreground text-xs">{k.last_used_at ? new Date(k.last_used_at).toLocaleDateString() : 'Never'}</td>
                  <td className="py-2.5">
                    <div className="flex gap-3">
                      <button onClick={() => { setRotatingId(k.key_id); setRevokeOld(true); }} title="Rotate" className="text-primary hover:opacity-70 p-0.5">
                        <RefreshCw className="h-3.5 w-3.5" />
                      </button>
                      <button onClick={() => deleteMutation.mutate(k.key_id)} disabled={deleteMutation.isPending} title="Delete" className="text-destructive hover:opacity-70 disabled:opacity-40 p-0.5">
                        <Trash2 className="h-3.5 w-3.5" />
                      </button>
                    </div>
                  </td>
                </tr>
                {rotatingId === k.key_id && (
                  <tr>
                    <td colSpan={4} className="py-3">
                      <div className="bg-muted rounded-lg p-3 space-y-2">
                        <p className="text-xs font-medium">Rotate &quot;{k.name}&quot;</p>
                        <label className="flex items-center gap-2 text-xs">
                          <input type="checkbox" checked={revokeOld} onChange={(e) => setRevokeOld(e.target.checked)} />
                          Revoke the old key immediately
                        </label>
                        <p className="text-xs text-muted-foreground">
                          {revokeOld
                            ? 'The old key stops working as soon as the new key is issued.'
                            : 'Grace period: both keys stay valid so you can roll out the new key, then revoke the old one manually.'}
                        </p>
                        <div className="flex gap-2 justify-end">
                          <button onClick={() => setRotatingId(null)} className="text-xs px-3 py-1.5 border border-border rounded-md hover:bg-card">Cancel</button>
                          <button onClick={() => rotateMutation.mutate(k.key_id)} disabled={rotateMutation.isPending} className="text-xs px-3 py-1.5 bg-primary text-primary-foreground rounded-md disabled:opacity-50">
                            {rotateMutation.isPending ? 'Rotating…' : 'Confirm rotation'}
                          </button>
                        </div>
                      </div>
                    </td>
                  </tr>
                )}
              </Fragment>
            ))}
          </tbody>
        </table>
      )}
    </SectionShell>
  );
}
```

Update the top imports of the file: add `Fragment` to the React import (`import React, { useState, useEffect, Fragment } from 'react';`) and remove the now-unused `apiKey` prop where `ApiKeysSection` is rendered in `SettingsPage` (change `<ApiKeysSection apiKey={apiKey} />` to `<ApiKeysSection />`). Leave `ProfileSection` and `LLMProviderSection` unchanged (they still use the local `apiFetch`).

- [ ] **Step 4: Run test to verify it passes**

Run: `npm run test -- src/features/settings/SettingsPage.test.tsx && npm run typecheck`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/features/settings/SettingsPage.tsx src/features/settings/SettingsPage.test.tsx
git commit -m "fix(settings): API-key rotation wizard reads new_key.raw_key via typed client

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

### Task 9: Emergency-stop store + client methods

**Files:**
- Create: `src/stores/emergency.ts`, `src/stores/emergency.test.ts`
- Modify: `src/lib/api/client.ts` (`governanceApi.emergencyStop` + `governanceApi.clearEmergencyStop`)
- Test: `src/lib/api/client.test.ts` (append)

**Context (verified):** backend exposes `POST /governance/emergency-stop` (activate) and `DELETE /governance/emergency-stop` (clear) — **but no GET status endpoint**. We therefore track the active state client-side in a persisted `useEmergencyStore` (set on activate, cleared on clear) and render a banner from it. The store persists to sessionStorage so the banner survives reloads within the session.

**Interfaces:**
- Produces (store): `useEmergencyStore` with `{ active: boolean; activatedAt: string | null; cancelledGoals: number; setActive(info: { cancelledGoals: number }): void; setCleared(): void }`.
- Produces (client):
  - `interface EmergencyStopResult { status: string; tenant_id?: string; cancelled_goals?: number; rejected_approvals?: number; message?: string }`
  - `governanceApi.emergencyStop(): Promise<EmergencyStopResult>` → `POST /governance/emergency-stop`
  - `governanceApi.clearEmergencyStop(): Promise<EmergencyStopResult>` → `DELETE /governance/emergency-stop`

- [ ] **Step 1: Write the failing tests**

```ts
// src/stores/emergency.test.ts
import { beforeEach, expect, test } from 'vitest';
import { useEmergencyStore } from '@/stores/emergency';

beforeEach(() => {
  sessionStorage.clear();
  useEmergencyStore.setState({ active: false, activatedAt: null, cancelledGoals: 0 });
});

test('setActive marks active with a cancelled count', () => {
  useEmergencyStore.getState().setActive({ cancelledGoals: 3 });
  const s = useEmergencyStore.getState();
  expect(s.active).toBe(true);
  expect(s.cancelledGoals).toBe(3);
  expect(s.activatedAt).not.toBeNull();
});

test('setCleared resets the state', () => {
  useEmergencyStore.getState().setActive({ cancelledGoals: 3 });
  useEmergencyStore.getState().setCleared();
  expect(useEmergencyStore.getState().active).toBe(false);
});
```

```ts
// append to src/lib/api/client.test.ts
import { governanceApi } from '@/lib/api/client';

test('emergencyStop POSTs to /governance/emergency-stop', async () => {
  const f = vi.spyOn(globalThis, 'fetch').mockResolvedValue(
    new Response(JSON.stringify({ status: 'emergency_stop_activated', cancelled_goals: 2 }), { status: 200, headers: { 'Content-Type': 'application/json' } })
  );
  const res = await governanceApi.emergencyStop();
  const [url, init] = f.mock.calls[0];
  expect(String(url)).toContain('/governance/emergency-stop');
  expect(init?.method).toBe('POST');
  expect(res.cancelled_goals).toBe(2);
});

test('clearEmergencyStop DELETEs /governance/emergency-stop', async () => {
  const f = vi.spyOn(globalThis, 'fetch').mockResolvedValue(
    new Response(JSON.stringify({ status: 'cleared' }), { status: 200, headers: { 'Content-Type': 'application/json' } })
  );
  await governanceApi.clearEmergencyStop();
  expect((f.mock.calls[0][1] as RequestInit)?.method).toBe('DELETE');
});
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `npm run test -- src/stores/emergency.test.ts src/lib/api/client.test.ts -t "emergencyStop\|clearEmergencyStop\|Emergency"`
Expected: FAIL — store/methods missing.

- [ ] **Step 3: Implement the store**

```ts
// src/stores/emergency.ts
import { create } from 'zustand';
import { persist, createJSONStorage } from 'zustand/middleware';

interface EmergencyState {
  active: boolean;
  activatedAt: string | null;
  cancelledGoals: number;
  setActive: (info: { cancelledGoals: number }) => void;
  setCleared: () => void;
}

export const useEmergencyStore = create<EmergencyState>()(
  persist(
    (set) => ({
      active: false,
      activatedAt: null,
      cancelledGoals: 0,
      setActive: ({ cancelledGoals }) =>
        set({ active: true, activatedAt: new Date().toISOString(), cancelledGoals }),
      setCleared: () => set({ active: false, activatedAt: null, cancelledGoals: 0 }),
    }),
    { name: 'av-emergency', storage: createJSONStorage(() => sessionStorage) }
  )
);
```

- [ ] **Step 4: Implement the client methods**

In `src/lib/api/client.ts`, add the type near the other governance types and extend `governanceApi`:

```ts
export interface EmergencyStopResult {
  status: string;
  tenant_id?: string;
  cancelled_goals?: number;
  rejected_approvals?: number;
  message?: string;
}
```

Add to the `governanceApi` object:

```ts
  emergencyStop: () =>
    request<EmergencyStopResult>("/governance/emergency-stop", { method: "POST" }),
  clearEmergencyStop: () =>
    request<EmergencyStopResult>("/governance/emergency-stop", { method: "DELETE" }),
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `npm run test -- src/stores/emergency.test.ts src/lib/api/client.test.ts && npm run typecheck`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add src/stores/emergency.ts src/stores/emergency.test.ts src/lib/api/client.ts src/lib/api/client.test.ts
git commit -m "feat(governance): emergency-stop client methods + client-side state store

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

### Task 10: Emergency-stop button (confirm modal) in the TopBar

**Files:**
- Create: `src/components/ui/EmergencyStopButton.tsx`, `src/components/ui/EmergencyStopButton.test.tsx`
- Modify: `src/components/ui/TopBar.tsx` (mount the button)

**Context:** P0-1. A destructive control with a confirmation modal that calls `governanceApi.emergencyStop()`, then sets `useEmergencyStore` active. When already active, the button label flips to "Clear stop" and calls `governanceApi.clearEmergencyStop()`.

**Interfaces:**
- Consumes: `governanceApi.emergencyStop`, `governanceApi.clearEmergencyStop`, `useEmergencyStore`, `toast`.
- Produces: named export `EmergencyStopButton`.

- [ ] **Step 1: Write the failing test**

```tsx
// src/components/ui/EmergencyStopButton.test.tsx
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, beforeEach, expect, test, vi } from 'vitest';
import { useEmergencyStore } from '@/stores/emergency';
import { EmergencyStopButton } from './EmergencyStopButton';

function renderBtn() {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(<QueryClientProvider client={qc}><EmergencyStopButton /></QueryClientProvider>);
}

beforeEach(() => {
  sessionStorage.clear();
  useEmergencyStore.setState({ active: false, activatedAt: null, cancelledGoals: 0 });
});
afterEach(() => vi.restoreAllMocks());

test('opens a confirm modal and activates emergency stop on confirm', async () => {
  const f = vi.spyOn(globalThis, 'fetch').mockResolvedValue(
    new Response(JSON.stringify({ status: 'emergency_stop_activated', cancelled_goals: 4 }), { status: 200, headers: { 'Content-Type': 'application/json' } })
  );
  renderBtn();
  await userEvent.click(screen.getByRole('button', { name: /emergency stop/i }));
  expect(await screen.findByRole('dialog')).toBeInTheDocument();
  await userEvent.click(screen.getByRole('button', { name: /confirm|stop everything/i }));
  await waitFor(() => expect(useEmergencyStore.getState().active).toBe(true));
  expect((f.mock.calls[0][1] as RequestInit)?.method).toBe('POST');
});

test('cancel closes the modal without calling the API', async () => {
  const f = vi.spyOn(globalThis, 'fetch');
  renderBtn();
  await userEvent.click(screen.getByRole('button', { name: /emergency stop/i }));
  await userEvent.click(screen.getByRole('button', { name: /cancel/i }));
  await waitFor(() => expect(screen.queryByRole('dialog')).not.toBeInTheDocument());
  expect(f).not.toHaveBeenCalled();
});

test('when active, the button clears the stop', async () => {
  useEmergencyStore.setState({ active: true, activatedAt: new Date().toISOString(), cancelledGoals: 1 });
  const f = vi.spyOn(globalThis, 'fetch').mockResolvedValue(
    new Response(JSON.stringify({ status: 'cleared' }), { status: 200, headers: { 'Content-Type': 'application/json' } })
  );
  renderBtn();
  await userEvent.click(screen.getByRole('button', { name: /clear stop/i }));
  await waitFor(() => expect(useEmergencyStore.getState().active).toBe(false));
  expect((f.mock.calls[0][1] as RequestInit)?.method).toBe('DELETE');
});
```

- [ ] **Step 2: Run test to verify it fails**

Run: `npm run test -- src/components/ui/EmergencyStopButton.test.tsx`
Expected: FAIL — module not found.

- [ ] **Step 3: Implement**

```tsx
// src/components/ui/EmergencyStopButton.tsx
import { useState } from "react";
import { useMutation } from "@tanstack/react-query";
import { OctagonAlert } from "lucide-react";
import { governanceApi } from "@/lib/api/client";
import { useEmergencyStore } from "@/stores/emergency";
import { toast } from "@/stores/toast";

export function EmergencyStopButton() {
  const [confirming, setConfirming] = useState(false);
  const active = useEmergencyStore((s) => s.active);
  const setActive = useEmergencyStore((s) => s.setActive);
  const setCleared = useEmergencyStore((s) => s.setCleared);

  const stopMutation = useMutation({
    mutationFn: () => governanceApi.emergencyStop(),
    onSuccess: (res) => {
      setActive({ cancelledGoals: res.cancelled_goals ?? 0 });
      setConfirming(false);
      toast({ kind: "error", message: `Emergency stop activated — ${res.cancelled_goals ?? 0} goal(s) cancelled.` });
    },
    onError: (e) => toast({ kind: "error", message: `Emergency stop failed: ${String(e)}` }),
  });

  const clearMutation = useMutation({
    mutationFn: () => governanceApi.clearEmergencyStop(),
    onSuccess: () => {
      setCleared();
      toast({ kind: "success", message: "Emergency stop cleared — new goals can run." });
    },
    onError: (e) => toast({ kind: "error", message: `Could not clear: ${String(e)}` }),
  });

  if (active) {
    return (
      <button
        onClick={() => clearMutation.mutate()}
        disabled={clearMutation.isPending}
        className="inline-flex items-center gap-1.5 px-2.5 py-1 text-xs font-semibold rounded-md border border-destructive text-destructive hover:bg-destructive/10 disabled:opacity-50"
      >
        <OctagonAlert className="h-3.5 w-3.5" aria-hidden="true" />
        {clearMutation.isPending ? "Clearing…" : "Clear stop"}
      </button>
    );
  }

  return (
    <>
      <button
        onClick={() => setConfirming(true)}
        aria-label="Emergency stop"
        className="inline-flex items-center gap-1.5 px-2.5 py-1 text-xs font-semibold rounded-md bg-destructive text-white hover:opacity-90"
      >
        <OctagonAlert className="h-3.5 w-3.5" aria-hidden="true" />
        <span className="hidden sm:inline">Emergency stop</span>
      </button>

      {confirming && (
        <div className="fixed inset-0 z-[200] flex items-center justify-center bg-black/50 px-4" role="dialog" aria-modal="true" aria-label="Confirm emergency stop">
          <div className="bg-card border border-border rounded-xl p-6 max-w-md w-full shadow-xl">
            <h2 className="text-lg font-semibold text-destructive mb-2">Activate emergency stop?</h2>
            <p className="text-sm text-muted-foreground mb-4">
              This immediately cancels all running and queued goals and rejects pending approvals for
              this tenant. Cancelled goals must be resubmitted. This action is logged to the audit trail.
            </p>
            {stopMutation.isError && <p className="text-xs text-destructive mb-2">Failed — please retry.</p>}
            <div className="flex justify-end gap-2">
              <button onClick={() => setConfirming(false)} className="px-4 py-2 text-sm border border-border rounded-md hover:bg-muted">
                Cancel
              </button>
              <button
                onClick={() => stopMutation.mutate()}
                disabled={stopMutation.isPending}
                className="px-4 py-2 text-sm bg-destructive text-white rounded-md hover:opacity-90 disabled:opacity-50"
              >
                {stopMutation.isPending ? "Stopping…" : "Stop everything"}
              </button>
            </div>
          </div>
        </div>
      )}
    </>
  );
}
```

- [ ] **Step 4: Mount in TopBar**

In `src/components/ui/TopBar.tsx`, import and render the button as the first item in the right-hand controls cluster (before the plan badge):

```tsx
import { EmergencyStopButton } from "@/components/ui/EmergencyStopButton";
// ...
      <div className="flex items-center gap-3">
        <EmergencyStopButton />
        {/* Plan badge */}
```

- [ ] **Step 5: Run test to verify it passes**

Run: `npm run test -- src/components/ui/EmergencyStopButton.test.tsx && npm run typecheck`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add src/components/ui/EmergencyStopButton.tsx src/components/ui/EmergencyStopButton.test.tsx src/components/ui/TopBar.tsx
git commit -m "feat(governance): emergency-stop control with confirm modal in TopBar

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

### Task 11: Emergency-stop live status banner

**Files:**
- Create: `src/components/ui/EmergencyStopBanner.tsx`, `src/components/ui/EmergencyStopBanner.test.tsx`
- Modify: `src/components/ui/AppLayout.tsx` (render the banner above routed content)

**Context:** A persistent, high-visibility banner shown app-wide whenever `useEmergencyStore.active` is true, reflecting the emergency-stop status (the closest we can do given there is no backend status endpoint). It offers an inline "Clear" action.

**Interfaces:**
- Consumes: `useEmergencyStore`, `governanceApi.clearEmergencyStop`, `toast`.
- Produces: named export `EmergencyStopBanner`. Renders `null` when not active.

- [ ] **Step 1: Write the failing test**

```tsx
// src/components/ui/EmergencyStopBanner.test.tsx
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { render, screen } from '@testing-library/react';
import { beforeEach, expect, test, vi } from 'vitest';
import { useEmergencyStore } from '@/stores/emergency';
import { EmergencyStopBanner } from './EmergencyStopBanner';

function renderBanner() {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(<QueryClientProvider client={qc}><EmergencyStopBanner /></QueryClientProvider>);
}

beforeEach(() => {
  sessionStorage.clear();
  useEmergencyStore.setState({ active: false, activatedAt: null, cancelledGoals: 0 });
});

test('renders nothing when not active', () => {
  const { container } = renderBanner();
  expect(container).toBeEmptyDOMElement();
});

test('renders the banner with the cancelled count when active', () => {
  useEmergencyStore.setState({ active: true, activatedAt: new Date().toISOString(), cancelledGoals: 5 });
  renderBanner();
  expect(screen.getByRole('alert')).toHaveTextContent(/emergency stop active/i);
  expect(screen.getByText(/5/)).toBeInTheDocument();
});
```

- [ ] **Step 2: Run test to verify it fails**

Run: `npm run test -- src/components/ui/EmergencyStopBanner.test.tsx`
Expected: FAIL — module not found.

- [ ] **Step 3: Implement**

```tsx
// src/components/ui/EmergencyStopBanner.tsx
import { useMutation } from "@tanstack/react-query";
import { OctagonAlert } from "lucide-react";
import { governanceApi } from "@/lib/api/client";
import { useEmergencyStore } from "@/stores/emergency";
import { toast } from "@/stores/toast";

export function EmergencyStopBanner() {
  const active = useEmergencyStore((s) => s.active);
  const cancelledGoals = useEmergencyStore((s) => s.cancelledGoals);
  const setCleared = useEmergencyStore((s) => s.setCleared);

  const clearMutation = useMutation({
    mutationFn: () => governanceApi.clearEmergencyStop(),
    onSuccess: () => {
      setCleared();
      toast({ kind: "success", message: "Emergency stop cleared." });
    },
    onError: (e) => toast({ kind: "error", message: `Could not clear: ${String(e)}` }),
  });

  if (!active) return null;

  return (
    <div role="alert" className="flex items-center justify-between gap-3 bg-destructive text-white px-4 py-2 text-sm">
      <div className="flex items-center gap-2">
        <OctagonAlert className="h-4 w-4 shrink-0" aria-hidden="true" />
        <span className="font-semibold">Emergency stop active</span>
        <span className="opacity-90">— {cancelledGoals} goal(s) were cancelled. New goals are blocked.</span>
      </div>
      <button
        onClick={() => clearMutation.mutate()}
        disabled={clearMutation.isPending}
        className="shrink-0 px-3 py-1 text-xs font-semibold rounded-md bg-white/15 hover:bg-white/25 disabled:opacity-50"
      >
        {clearMutation.isPending ? "Clearing…" : "Clear"}
      </button>
    </div>
  );
}
```

- [ ] **Step 4: Mount in AppLayout**

Read `src/components/ui/AppLayout.tsx` first to find where `<TopBar />` and the routed `<Outlet />`/children render. Render `<EmergencyStopBanner />` immediately below `<TopBar />` and above the main content. Add the import:

```tsx
import { EmergencyStopBanner } from "@/components/ui/EmergencyStopBanner";
```

and place `<EmergencyStopBanner />` directly after the `<TopBar />` element in the layout's JSX (so the banner spans the content column, under the top bar).

- [ ] **Step 5: Run test + typecheck**

Run: `npm run test -- src/components/ui/EmergencyStopBanner.test.tsx && npm run typecheck`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add src/components/ui/EmergencyStopBanner.tsx src/components/ui/EmergencyStopBanner.test.tsx src/components/ui/AppLayout.tsx
git commit -m "feat(governance): app-wide emergency-stop status banner

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

### Task 12: E2E — SSO feature-detect + callback, and emergency-stop flow

**Files:**
- Create: `e2e/auth-sso.spec.ts`, `e2e/emergency-stop.spec.ts`

**Context:** Reuse the `setupAuth(page)` + `page.route(...)` mocking pattern from `e2e/goals.spec.ts`. Mock `/auth/config`, `/auth/token`, `/auth/userinfo`, and `/governance/emergency-stop`.

- [ ] **Step 1: Write `e2e/auth-sso.spec.ts`**

```ts
// e2e/auth-sso.spec.ts
import { test, expect, type Page } from '@playwright/test';

async function mockSsoEnabled(page: Page) {
  await page.route('**/auth/config', (route) =>
    route.fulfill({
      status: 200, contentType: 'application/json',
      body: JSON.stringify({ sso_enabled: true, provider: 'keycloak', keycloak_url: 'http://kc', realm: 'r', client_id: 'c', authorization_endpoint: 'http://kc/auth', token_endpoint: 'http://kc/token' }),
    })
  );
}

test('SSO button appears when config is enabled', async ({ page }) => {
  await mockSsoEnabled(page);
  await page.goto('/auth');
  await expect(page.getByRole('button', { name: /sign in with sso/i })).toBeVisible({ timeout: 10000 });
});

test('SSO button is hidden when config is disabled', async ({ page }) => {
  await page.route('**/auth/config', (route) =>
    route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify({ sso_enabled: false }) })
  );
  await page.goto('/auth');
  await expect(page.locator('#apiKey')).toBeVisible({ timeout: 10000 });
  await expect(page.getByRole('button', { name: /sign in with sso/i })).toHaveCount(0);
});

test('callback exchanges the code and lands on the dashboard', async ({ page }) => {
  // Pre-set the expected state so the CSRF guard passes
  await page.addInitScript(() => sessionStorage.setItem('av_sso_state', 'st'));
  await page.route('**/auth/token**', (route) =>
    route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify({ access_token: 'AT', refresh_token: 'RT', expires_in: 300, token_type: 'Bearer' }) })
  );
  await page.route('**/auth/userinfo', (route) =>
    route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify({ sub: 'user-1', email: 'u@x.co', roles: [], email_verified: true }) })
  );
  await page.route('**/tenants/me', (route) =>
    route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify({ tenant_id: 'user-1', name: 'SSO User', plan: 'sso' }) })
  );
  await page.route(/localhost:8000\/goals/, (route) =>
    route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify({ goals: [] }) })
  );
  await page.goto('/auth/callback?code=CODE&state=st');
  await expect(page).toHaveURL(/\/dashboard/, { timeout: 15000 });
});
```

- [ ] **Step 2: Write `e2e/emergency-stop.spec.ts`**

```ts
// e2e/emergency-stop.spec.ts
import { test, expect, type Page } from '@playwright/test';

async function setupAuth(page: Page) {
  await page.addInitScript(() => {
    localStorage.setItem('av-auth', JSON.stringify({ state: { apiKey: 'test-key', tenantId: 'test-tenant', plan: 'free', isAuthenticated: true }, version: 0 }));
    localStorage.setItem('av_api_key', 'test-key');
  });
  await page.route('**/tenants/me', (route) =>
    route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify({ tenant_id: 'test-tenant', name: 'Test Org', plan: 'free' }) })
  );
  await page.route(/localhost:8000\/goals/, (route) =>
    route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify({ goals: [] }) })
  );
  await page.route('**/auth/config', (route) =>
    route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify({ sso_enabled: false }) })
  );
}

test('confirm modal activates emergency stop and shows the banner; clear hides it', async ({ page }) => {
  await setupAuth(page);
  await page.route('**/governance/emergency-stop', (route) => {
    const method = route.request().method();
    if (method === 'POST')
      return route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify({ status: 'emergency_stop_activated', cancelled_goals: 2 }) });
    return route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify({ status: 'cleared' }) });
  });

  await page.goto('/dashboard');
  await page.getByRole('button', { name: /emergency stop/i }).click();
  await expect(page.getByRole('dialog')).toBeVisible();
  await page.getByRole('button', { name: /stop everything/i }).click();

  await expect(page.getByRole('alert').filter({ hasText: /emergency stop active/i })).toBeVisible({ timeout: 10000 });

  await page.getByRole('button', { name: /^clear$/i }).click();
  await expect(page.getByText(/emergency stop active/i)).toHaveCount(0, { timeout: 10000 });
});
```

- [ ] **Step 3: Run the e2e specs**

Run: `npm run test:e2e -- e2e/auth-sso.spec.ts e2e/emergency-stop.spec.ts`
Expected: PASS. (If the dev server is not auto-started by the Playwright config, start it per the repo's existing e2e convention, then re-run.)

- [ ] **Step 4: Commit**

```bash
git add e2e/auth-sso.spec.ts e2e/emergency-stop.spec.ts
git commit -m "test(e2e): SSO feature-detect/callback and emergency-stop flows

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

### Task 13: Phase-2 regression gate

**Files:** none (verification only)

- [ ] **Step 1: Typecheck**

Run: `npm run typecheck`
Expected: no errors.

- [ ] **Step 2: Lint**

Run: `npm run lint`
Expected: no errors (pre-existing warnings acceptable).

- [ ] **Step 3: Full unit suite**

Run: `npm run test`
Expected: all pass; coverage not decreased.

- [ ] **Step 4: E2E (existing + new must pass)**

Run: `npm run test:e2e -- e2e/auth.spec.ts e2e/navigation.spec.ts e2e/auth-sso.spec.ts e2e/emergency-stop.spec.ts`
Expected: PASS. The existing API-key login path on `AuthPage` is unchanged, so `e2e/auth.spec.ts` stays green; the new TopBar control does not alter existing nav.

- [ ] **Step 5: Tag the phase**

```bash
git tag -a frontend-phase2 -m "Frontend Phase 2: auth & safety controls"
```

---

## Self-Review

**Spec coverage (against WS-1 + P0-1):**
- SSO flow (config feature-detect → login redirect → `/auth/callback` exchange → userinfo hydration) → Tasks 1, 4, 5. `authApi` owned here. ✅
- Token refresh loop (refresh before expiry; API-key auth unchanged) → Tasks 2, 3. ✅
- Dead signup link → `tenantsApi.signup` → Task 4 (`SignupForm`). ✅
- API-key rotation wizard (list/create/revoke/rotate, revoke-old toggle + grace note) → Task 8 (also fixes the verified `new_key.raw_key` bug). BYOK vault-key form → Tasks 6, 7. ✅
- P0-1 Emergency Stop in TopBar (confirm modal) + live status banner → Tasks 9, 10, 11. ✅

**Grounding corrections folded in (vs. assumptions):**
- `POST /auth/token` and `POST /auth/refresh` take **query-string** params (`code`/`redirect_uri`, `refresh_token_value`), not JSON bodies (`auth.py:68-136`) — `authApi` builds query strings accordingly.
- The rotate endpoint returns a **nested `new_key`** object, not a flat `raw_key` (`tenants.py:170-173`); the existing SettingsPage rotate was reading `data.raw_key` → `undefined`. Task 8 fixes this.
- **No `GET /governance/emergency-stop` status endpoint exists** (only POST/DELETE, `governance.py:584,695`); the "live status banner" is therefore driven by a persisted client-side `useEmergencyStore`, not a backend poll. This is the most faithful implementation possible without a backend change (out of scope for this phase).
- `tenantsApi.signup` posts `{ name, email }` only (backend `SignupRequest` has no `plan`, `tenants.py:49-51`).

**Reuse from Phase 1:** `toast`/`useToastStore` used for all user feedback; the client's existing 401→logout interceptor remains the API-key safety net; `Skeleton`/`EmptyState`/`StatusBadge` available (loading states here are simple text, consistent with sibling sections).

**Interface ownership:** this phase OWNS `authApi` (Task 1) and the `/auth/callback` route (Task 5). It did NOT create `src/lib/sse/useEventStream.ts` (Phase 3 owns it).

**Type consistency:** `SsoConfig`/`SsoTokens`/`UserInfo` defined once in `client.ts` and consumed by `useSsoStore`, `refreshScheduler`, `AuthCallbackPage`; `CreatedApiKey`/`RotateKeyResult` consumed by `SettingsPage`; `EmergencyStopResult` consumed by the button/banner; `useEmergencyStore` shape consistent across store/button/banner.

**Placeholder scan:** none — every code step contains complete code; every run step has an exact command + expected result.

---

## Execution Handoff

Phase 2 depends on Phase 1 (`toast`, 401 interceptor). It lands `authApi`, the `/auth/callback` route, the SSO token store + refresh scheduler, the emergency-stop store + client methods, and the Settings rotation/BYOK UI. Phase 3 (WS-2) will build on `useEmergencyStore`/`governanceApi` for real-time governance and will introduce the shared SSE hook (`src/lib/sse/useEventStream.ts`) that this phase deliberately did not create.
