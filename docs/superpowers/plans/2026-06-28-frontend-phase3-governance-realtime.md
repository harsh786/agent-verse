# Frontend Phase 3 — Governance & Real-time Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make governance fully operable and real-time from the UI. Ship a reusable generic SSE hook (`useEventStream`), two **additive backend SSE endpoints** wrapping the existing Redis pub/sub (`/governance/approvals/stream`, `/governance/policies/stream`), a Notification Center, real-time auto-updating approvals + a live pending-approvals counter in the TopBar, a typed Audit Explorer (filters + CSV/JSON export — replaces the `any[]` stub), a Legal-hold + GDPR async-export (job polling) + consent-management page, and an RBAC roles + IP-allowlist management page. All new client methods are added to the typed client; all backend changes are additive.

**Architecture:** Frontend changes in `agent-verse-frontend` plus **additive-only** backend in `agent-verse-backend`. The two SSE endpoints mirror the goal-stream pattern (`app/api/goals.py:236` → `StreamingResponse` + `text/event-stream`) and consume the existing Redis pub/sub channels (`policy_changes` from `PolicyEngine.publish_change`, `app/governance/policies.py:189`; `platform_events:{tenant_id}` from `GoalService._dispatch_event`, `app/services/goal_service.py:1156`). Two small additive governance routes are added (`DELETE /governance/notifications/{channel_id}` and `GET /governance/legal-holds`) because the service method (`NotificationService.remove_channel`, `app/services/notification_service.py:44`) and the table exist but no route does. Strict TDD: vitest + Testing Library on the frontend, pytest (`TestClient`/`AsyncClient`) on the backend; one commit per task.

**Tech Stack:** React 19, TypeScript (strict), Vite, TanStack Query 5, Zustand 5, Tailwind, react-router-dom 7, vitest 3 + @testing-library/react, Playwright. Backend: FastAPI, Starlette `StreamingResponse`, `redis.asyncio`, pytest.

## Global Constraints

- **Additive backend only.** Add two SSE GET routes + two small governance routes (DELETE channel, GET legal-holds). Do **not** modify any existing endpoint, table, or service signature. New routes ship with pytest.
- **Reuse Phase-1 foundations:** `toast` / `useToastStore` from `@/stores/toast`; the typed client `@/lib/api/client` (ADD methods there, never inline `fetch` in pages); 401→logout is already handled inside `client.ts` `request()`; `Skeleton`, `EmptyState`, `StatusBadge` from `@/components/ui`.
- **Auth access is via `useAuthStore` (`@/stores/auth`)** — never read `localStorage`/`sessionStorage` for the API key directly in pages. The SSE hook reads the key the same way `useGoalStream.ts:44-47` does (`sessionStorage ?? localStorage ?? ''`).
- **SSE hooks** are modeled on `src/lib/sse/useGoalStream.ts`: fetch + `X-API-Key` header (native `EventSource` cannot set headers), `ReadableStream` reader, frames split on `\n\n`, exponential backoff (1s…30s, max 8 attempts), retries cancelled on terminal events and unmount.
- **Verified backend ground truth (file:line):**
  - HITL approvals list: `GET /governance/approvals` (`governance.py:381`) → `[{request_id, goal_id, action, risk_level, status}]`.
  - Approve/reject require `{approver: str, note?: str}` (`governance.py:35-37, 398, 418`); both gated by `require_role("approver")`.
  - Audit query: `GET /governance/audit` (`governance.py:442`) params `goal_id, tool_name, limit, offset, start_time, end_time` → `[{event_id, goal_id, tool_name, action_level, outcome, step_id, approver, note}]`.
  - Notification channels: `POST /governance/notifications` (`governance.py:525`) body `{channel_type, config}` → `{channel_id, type, status}`; `GET /governance/notifications` (`governance.py:544`) → `[{channel_id, type, enabled}]`. **No DELETE route and no delivery-logs route exist** (service has `remove_channel`; no delivery log is recorded) — Task 4 adds DELETE; delivery logs are surfaced as "not yet available" with feature-detect.
  - Legal hold: `POST /governance/legal-hold` (`governance.py:812`) body `{reason, expires_at?}`. **No GET exists** — Task 9 adds `GET /governance/legal-holds`.
  - GDPR export (async): `POST /compliance/export/start` (`enterprise.py:451`) → `{job_id, status, poll_url}`; poll `GET /compliance/export/jobs/{job_id}` (`enterprise.py:485`) → `{job_id, status, completed_at, download_url, error}`. Router prefix is `/compliance` (`enterprise.py:15`, mounted `main.py:848`).
  - Consent: `POST /compliance/consent` (`enterprise.py:515`) body `{purpose, legal_basis?}` → `{consent_id, purpose, status}`; `DELETE /compliance/consent/{purpose}` (`enterprise.py:542`) → `{purpose, status}`.
  - RBAC roles: `GET /tenants/me/roles` (`tenants.py:267`) → `[{id, user_id, role, created_at}]`; `POST /tenants/me/roles` (`tenants.py:301`) body `{user_id, role}` (role ∈ VALID_ROLES) → `{id, user_id, role, tenant_id}`; `DELETE /tenants/me/roles/{role_id}` (`tenants.py:342`) → 204.
  - IP allowlist: `GET /tenants/me/ip-allowlist` (`tenants.py:396`) → `[{id, cidr, description, created_at}]`; `POST /tenants/me/ip-allowlist` (`tenants.py:432`) body `{cidr, description?}` → `{id, cidr, description}`; `DELETE /tenants/me/ip-allowlist/{entry_id}` (`tenants.py:495`) → 204.
  - Redis pub/sub source channels: `policy_changes` (`policies.py:189`); `platform_events:{tenant_id}` (`goal_service.py:1156`); `app.state._policy_pubsub_redis` is wired in `main.py:656`.
- **Tailwind design tokens only:** `bg-card`, `border-border`, `text-primary`, `text-muted-foreground`, `bg-muted`, `text-destructive`, with `dark:` variants where siblings use them.
- **Quality gate per frontend task:** `npm run typecheck` and `npm run lint` and `npm run test` pass before commit. **Per backend task:** `uv run pytest <file>`, `uv run ruff check`, and `uv run mypy app` pass.
- **New pages:** add a route in `src/app/App.tsx` and a nav entry in `src/components/ui/Sidebar.tsx`.
- **Commit style:** conventional commits; end every commit message with:
  `Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>`

---

## File Structure

**Create (frontend):**
- `src/lib/sse/useEventStream.ts` — generic reusable SSE hook (owned by this phase; consumed by later phases).
- `src/lib/sse/useEventStream.test.ts`
- `src/lib/api/governance-realtime.test.ts` — tests for the new client methods (`notificationsApi`, `auditApi`, `rbacApi`, plus `governanceApi.streamPath` helpers).
- `src/features/notifications/NotificationCenterPage.tsx` + `NotificationCenterPage.test.tsx`
- `src/features/rbac/RbacPage.tsx` + `RbacPage.test.tsx`
- `src/features/compliance/CompliancePage.tsx` + `CompliancePage.test.tsx` (legal-hold status + GDPR async export polling + consent management)
- `src/components/ui/PendingApprovalsBadge.tsx` + `PendingApprovalsBadge.test.tsx` (live TopBar counter)
- `e2e/notifications.spec.ts`, `e2e/audit-export.spec.ts`

**Modify (frontend):**
- `src/lib/api/client.ts` — add `notificationsApi`, `auditApi`, `rbacApi`; add typed `AuditEvent`, `NotificationChannel`, `RoleAssignment`, `IpAllowlistEntry`, `GdprExportJob` interfaces; add `governanceApi.approvalsStreamPath()` / `policiesStreamPath()` helpers.
- `src/features/audit/AuditExplorerPage.tsx` — replace `any[]` stub with typed model + filters + CSV/JSON export, via `auditApi`.
- `src/features/approvals/ApprovalsPage.tsx` — auto-update via `useEventStream` (invalidate query on event), drop blind 5s polling fallback to a slower 30s safety net.
- `src/components/ui/TopBar.tsx` — mount `<PendingApprovalsBadge />`.
- `src/app/App.tsx` — routes for `/notifications`, `/rbac`, `/compliance`.
- `src/components/ui/Sidebar.tsx` — nav entries for the three new pages.

**Create (backend, additive):**
- (none new) — endpoints are added into existing routers.

**Modify (backend, additive only):**
- `agent-verse-backend/app/api/governance.py` — add `GET /governance/approvals/stream` (SSE), `GET /governance/policies/stream` (SSE), `DELETE /governance/notifications/{channel_id}`, `GET /governance/legal-holds`.
- `agent-verse-backend/tests/api/test_governance_streams.py` (create) — pytest for the two SSE endpoints + DELETE channel + GET legal-holds.

**Out of scope (owned by other phases):** `GET /goals/{id}/decision-traces` (Phase 5 owns it; Phase 8 consumes). Emergency-stop UI, SSO, key-rotation (Phase 2). Memory/Artifacts/Tools (Phase 4). Dashboards depth (Phase 8).

---

## Test harness reference (existing patterns — reuse verbatim)

**Frontend component tests** (`src/features/goals/GoalsListPage.test.tsx`):

```tsx
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter } from 'react-router-dom';
import { vi } from 'vitest';
import { useAuthStore } from '@/stores/auth';

function renderWithProviders(ui: React.ReactNode) {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={queryClient}>
      <MemoryRouter>{ui}</MemoryRouter>
    </QueryClientProvider>
  );
}

beforeEach(() => {
  sessionStorage.clear();
  localStorage.clear();
  useAuthStore.setState({ apiKey: 'test-key', tenantId: 'test-tenant', plan: 'free', isAuthenticated: true });
});
```

Client/store unit tests mock `fetch` directly: `vi.spyOn(globalThis, 'fetch')`. SSE-hook tests mock a `ReadableStream` reader exactly as `src/lib/sse/useGoalStream.test.ts:33-58` does.

**Backend governance tests** (`tests/api/test_governance_api.py:21-39`): build a minimal `FastAPI`, add `TenantMiddleware` with a `key_resolver`, include the `governance_router`, set `app.state.{hitl_gateway, audit_log, cost_controller, policy_engine}`, then drive with `fastapi.testclient.TestClient` and an `X-API-Key` header. The `_CTX` fixture carries `roles=("admin",)`; add `"approver"` to roles where the approve route is exercised.

---

### Task 1: Generic `useEventStream` hook (OWNED — later phases consume)

**Files:**
- Create: `src/lib/sse/useEventStream.ts`, `src/lib/sse/useEventStream.test.ts`

**Context:** A path-generic version of `useGoalStream`. Takes an arbitrary path, sets `X-API-Key` from the same source as `useGoalStream.ts:44-47`, uses the same backoff (1s…30s, max 8), and returns `{ events, connected }`. Unlike `useGoalStream` it does **not** auto-stop on `goal_*` terminal events (governance streams are long-lived); instead the caller may pass `terminalTypes` to opt into stop-on-terminal behaviour.

**Interfaces:**
- Consumes: nothing.
- Produces:
  - `interface StreamEvent { type: string; [key: string]: unknown }`
  - `interface UseEventStreamOptions { onEvent?: (e: StreamEvent) => void; terminalTypes?: string[]; enabled?: boolean }`
  - `useEventStream(path: string | null, opts?: UseEventStreamOptions): { events: StreamEvent[]; connected: boolean }`

- [ ] **Step 1: Write the failing test**

```ts
// src/lib/sse/useEventStream.test.ts
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { renderHook, waitFor } from '@testing-library/react';
import { useEventStream } from './useEventStream';

describe('useEventStream', () => {
  beforeEach(() => { vi.clearAllMocks(); sessionStorage.clear(); localStorage.clear(); });

  it('starts disconnected and does not fetch when path is null', () => {
    const fetchSpy = vi.spyOn(globalThis, 'fetch');
    const { result } = renderHook(() => useEventStream(null));
    expect(result.current.connected).toBe(false);
    expect(result.current.events).toHaveLength(0);
    expect(fetchSpy).not.toHaveBeenCalled();
  });

  it('sends X-API-Key header and the given path', async () => {
    const fetchSpy = vi.spyOn(globalThis, 'fetch').mockResolvedValueOnce({
      ok: false, status: 401, body: null,
    } as Response);
    sessionStorage.setItem('av_api_key', 'key-xyz');
    renderHook(() => useEventStream('/governance/approvals/stream'));
    await waitFor(() => expect(fetchSpy).toHaveBeenCalled());
    const [url, init] = fetchSpy.mock.calls[0] as [string, RequestInit];
    expect(url).toContain('/governance/approvals/stream');
    expect(url).not.toContain('api_key=');
    expect((init.headers as Record<string, string>)['X-API-Key']).toBe('key-xyz');
  });

  it('parses SSE events and invokes onEvent', async () => {
    const frame = `data: ${JSON.stringify({ type: 'approval_pending', request_id: 'r1' })}\n\n`;
    const encoded = new TextEncoder().encode(frame);
    let n = 0;
    const reader = {
      read: vi.fn().mockImplementation(async () =>
        n++ === 0 ? { done: false, value: encoded } : { done: true, value: undefined }),
    };
    vi.spyOn(globalThis, 'fetch').mockResolvedValueOnce({
      ok: true, status: 200, body: { getReader: () => reader } as unknown as ReadableStream,
    } as Response);
    const onEvent = vi.fn();
    const { result } = renderHook(() => useEventStream('/x', { onEvent }));
    await waitFor(() => expect(result.current.events).toHaveLength(1));
    expect(result.current.events[0].type).toBe('approval_pending');
    expect(onEvent).toHaveBeenCalledWith(expect.objectContaining({ type: 'approval_pending' }));
  });

  it('does not fetch when enabled is false', () => {
    const fetchSpy = vi.spyOn(globalThis, 'fetch');
    renderHook(() => useEventStream('/x', { enabled: false }));
    expect(fetchSpy).not.toHaveBeenCalled();
  });
});
```

- [ ] **Step 2: Run test to verify it fails**

Run: `npm run test -- src/lib/sse/useEventStream.test.ts`
Expected: FAIL — module not found.

- [ ] **Step 3: Implement the hook**

```ts
// src/lib/sse/useEventStream.ts
/**
 * Generic reusable SSE hook. A path-generic version of useGoalStream:
 * fetch + X-API-Key (native EventSource cannot set headers), ReadableStream
 * reader, frames split on "\n\n", exponential backoff (1s..30s, max 8 attempts),
 * retries cancelled on terminalTypes events and unmount.
 */
import { useEffect, useRef, useState } from "react";

export interface StreamEvent {
  type: string;
  [key: string]: unknown;
}

export interface UseEventStreamOptions {
  onEvent?: (e: StreamEvent) => void;
  terminalTypes?: string[];
  enabled?: boolean;
}

const MAX_RETRIES = 8;
const MAX_BACKOFF_MS = 30000;

export function useEventStream(
  path: string | null,
  opts?: UseEventStreamOptions,
): { events: StreamEvent[]; connected: boolean } {
  const [events, setEvents] = useState<StreamEvent[]>([]);
  const [connected, setConnected] = useState(false);
  const abortRef = useRef<AbortController | null>(null);
  const onEventRef = useRef(opts?.onEvent);
  const retryCountRef = useRef(0);
  const retryTimerRef = useRef<ReturnType<typeof setTimeout>>();

  onEventRef.current = opts?.onEvent;
  const terminalTypes = opts?.terminalTypes ?? [];
  const enabled = opts?.enabled ?? true;

  useEffect(() => {
    if (!path || !enabled) return;
    retryCountRef.current = 0;

    const apiKey =
      sessionStorage.getItem("av_api_key") ??
      localStorage.getItem("av_api_key") ??
      "";
    const API_BASE_URL =
      (import.meta.env.VITE_API_URL as string | undefined) ?? "http://localhost:8000";
    const url = `${API_BASE_URL}${path}`;

    const scheduleReconnect = () => {
      if (retryCountRef.current >= MAX_RETRIES) {
        setConnected(false);
        return;
      }
      const delay = Math.min(1000 * Math.pow(2, retryCountRef.current), MAX_BACKOFF_MS);
      retryCountRef.current += 1;
      retryTimerRef.current = setTimeout(() => void startConnection(), delay);
    };

    const startConnection = async () => {
      const abort = new AbortController();
      abortRef.current = abort;
      let terminalReceived = false;
      try {
        const res = await fetch(url, {
          headers: { "X-API-Key": apiKey, Accept: "text/event-stream" },
          signal: abort.signal,
        });
        if (!res.ok || !res.body) {
          setConnected(false);
          scheduleReconnect();
          return;
        }
        setConnected(true);
        const reader = res.body.getReader();
        const decoder = new TextDecoder();
        let buffer = "";
        while (true) {
          const { done, value } = await reader.read();
          if (done) break;
          buffer += decoder.decode(value, { stream: true });
          const frames = buffer.split("\n\n");
          buffer = frames.pop() ?? "";
          for (const frame of frames) {
            for (const line of frame.split("\n")) {
              const data = line.startsWith("data: ") ? line.slice(6).trim() : null;
              if (!data) continue;
              try {
                const parsed = JSON.parse(data) as StreamEvent;
                setEvents((prev) => [...prev, parsed]);
                onEventRef.current?.(parsed);
                if (terminalTypes.includes(parsed.type)) {
                  retryCountRef.current = 0;
                  terminalReceived = true;
                  setConnected(false);
                }
              } catch {
                // ignore malformed JSON frames
              }
            }
          }
        }
        if (!terminalReceived) scheduleReconnect();
      } catch (err) {
        if ((err as Error).name !== "AbortError") scheduleReconnect();
      } finally {
        setConnected(false);
      }
    };

    void startConnection();
    return () => {
      clearTimeout(retryTimerRef.current);
      abortRef.current?.abort();
      abortRef.current = null;
      setConnected(false);
    };
    // terminalTypes/enabled are captured per-effect; path drives reconnection.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [path, enabled]);

  return { events, connected };
}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `npm run test -- src/lib/sse/useEventStream.test.ts`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/lib/sse/useEventStream.ts src/lib/sse/useEventStream.test.ts
git commit -m "feat(sse): reusable useEventStream hook for arbitrary SSE paths

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

### Task 2: Backend — `GET /governance/approvals/stream` (SSE)

**Files:**
- Modify: `agent-verse-backend/app/api/governance.py` (add route + helper near the approvals section, after `governance.py:435`)
- Test: `agent-verse-backend/tests/api/test_governance_streams.py` (create)

**Context:** Mirror the goal-stream pattern (`goals.py:236-275`): return a `StreamingResponse(media_type="text/event-stream")`. The generator first **emits a snapshot** (`{"type": "approvals_snapshot", "pending": [...]}`) from `HITLGateway.list_pending(tenant_ctx=...)` so a fresh subscriber gets current state, then tails the `platform_events:{tenant_id}` Redis channel (the channel `GoalService` publishes terminal/HITL events to, `goal_service.py:1156`). When Redis is unavailable, emit the snapshot then a `{"type": "stream_unavailable"}` event and end (frontend falls back to polling). Forward only governance-relevant event types (`waiting_approval`, `approval_granted`, `goal_complete`, `goal_failed`).

**Interfaces:**
- Consumes: `app.state.hitl_gateway` (`_hitl`), `app.state._policy_pubsub_redis` (optional).
- Produces: `GET /governance/approvals/stream` → `text/event-stream`; events: `approvals_snapshot`, then forwarded platform events, optional `stream_unavailable`.

- [ ] **Step 1: Write the failing test**

```python
# agent-verse-backend/tests/api/test_governance_streams.py
"""Tests for additive governance SSE + channel-delete + legal-hold-list endpoints."""
from __future__ import annotations

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.governance import router as governance_router
from app.governance.audit import AuditLog
from app.governance.cost import CostController
from app.governance.hitl import HITLGateway
from app.governance.policies import PolicyEngine
from app.tenancy.context import PlanTier, TenantContext
from app.tenancy.middleware import SecurityHeadersMiddleware, TenantMiddleware

_CTX = TenantContext(
    tenant_id="tid-stream", plan=PlanTier.PROFESSIONAL,
    api_key_id="kid-1", roles=("admin", "approver"),
)
_VALID_KEY = "av_test_streamkey"


def _make_app(hitl: HITLGateway | None = None) -> FastAPI:
    app = FastAPI()

    async def _resolve(key: str) -> TenantContext | None:
        return _CTX if key == _VALID_KEY else None

    app.add_middleware(TenantMiddleware, key_resolver=_resolve)
    app.add_middleware(SecurityHeadersMiddleware)
    app.include_router(governance_router)
    app.state.hitl_gateway = hitl or HITLGateway()
    app.state.audit_log = AuditLog()
    app.state.cost_controller = CostController()
    app.state.policy_engine = PolicyEngine()
    # No Redis wired → snapshot + stream_unavailable path
    app.state._policy_pubsub_redis = None
    return app


def test_approvals_stream_emits_snapshot_without_redis() -> None:
    client = TestClient(_make_app(), raise_server_exceptions=False)
    with client.stream(
        "GET", "/governance/approvals/stream", headers={"X-API-Key": _VALID_KEY}
    ) as resp:
        assert resp.status_code == 200
        assert "text/event-stream" in resp.headers["content-type"]
        body = ""
        for chunk in resp.iter_text():
            body += chunk
            if "stream_unavailable" in body:
                break
    assert "approvals_snapshot" in body
    assert "stream_unavailable" in body


def test_approvals_stream_requires_auth() -> None:
    client = TestClient(_make_app(), raise_server_exceptions=False)
    resp = client.get("/governance/approvals/stream")
    assert resp.status_code == 401
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd agent-verse-backend && uv run pytest tests/api/test_governance_streams.py::test_approvals_stream_emits_snapshot_without_redis -x`
Expected: FAIL — 404 (route does not exist).

- [ ] **Step 3: Implement the endpoint**

In `app/api/governance.py`, add imports at the top of the module (after the existing imports):

```python
import asyncio
import json as _json
from collections.abc import AsyncGenerator

from starlette.responses import StreamingResponse
```

Add, immediately after `reject_request` (`governance.py:435`):

```python
# ---------------------------------------------------------------------------
# Endpoints — real-time SSE streams (additive; wrap existing Redis pub/sub)
# ---------------------------------------------------------------------------

_SSE_HEADERS = {
    "Cache-Control": "no-cache",
    "X-Accel-Buffering": "no",
    "Connection": "keep-alive",
}

# Event types relevant to the approvals UI (forwarded from platform_events).
_APPROVAL_EVENT_TYPES = {
    "waiting_approval",
    "approval_granted",
    "goal_complete",
    "goal_failed",
}


def _pending_snapshot(gateway: HITLGateway, tenant_ctx: TenantContext) -> dict[str, Any]:
    pending = gateway.list_pending(tenant_ctx=tenant_ctx)
    return {
        "type": "approvals_snapshot",
        "pending": [
            {
                "request_id": r.request_id,
                "goal_id": r.goal_id,
                "action": r.action,
                "risk_level": r.risk_level,
                "status": r.status,
            }
            for r in pending
        ],
    }


async def _tail_redis_channel(
    redis: Any, channel: str, allowed_types: set[str] | None
) -> AsyncGenerator[str, None]:
    """Yield SSE frames from a Redis pub/sub channel. Closes cleanly on cancel."""
    pubsub = redis.pubsub()
    await pubsub.subscribe(channel)
    try:
        async for message in pubsub.listen():
            if message.get("type") != "message":
                continue
            raw = message.get("data")
            try:
                event = _json.loads(raw) if isinstance(raw, (str, bytes)) else raw
            except Exception:
                continue
            if not isinstance(event, dict):
                continue
            if allowed_types is not None and event.get("type") not in allowed_types:
                continue
            yield f"data: {_json.dumps(event)}\n\n"
    finally:
        with __import__("contextlib").suppress(Exception):
            await pubsub.unsubscribe(channel)
        with __import__("contextlib").suppress(Exception):
            await pubsub.close()


@router.get("/approvals/stream")
async def stream_approvals(request: Request) -> StreamingResponse:
    """SSE stream of HITL approval activity.

    Emits an `approvals_snapshot` event with current pending requests, then tails
    the `platform_events:{tenant_id}` Redis channel and forwards approval-relevant
    events. When Redis is unavailable, emits the snapshot then `stream_unavailable`.
    """
    tenant_ctx: TenantContext = _require_tenant(request)
    gateway = _hitl(request)
    redis = getattr(request.app.state, "_policy_pubsub_redis", None)

    async def gen() -> AsyncGenerator[str, None]:
        yield f"data: {_json.dumps(_pending_snapshot(gateway, tenant_ctx))}\n\n"
        if redis is None:
            yield f'data: {_json.dumps({"type": "stream_unavailable"})}\n\n'
            return
        channel = f"platform_events:{tenant_ctx.tenant_id}"
        async for frame in _tail_redis_channel(redis, channel, _APPROVAL_EVENT_TYPES):
            yield frame

    return StreamingResponse(gen(), media_type="text/event-stream", headers=_SSE_HEADERS)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd agent-verse-backend && uv run pytest tests/api/test_governance_streams.py -k approvals -x`
Expected: PASS (both approvals tests).

- [ ] **Step 5: Lint + type-check**

Run: `cd agent-verse-backend && uv run ruff check app/api/governance.py && uv run mypy app/api/governance.py`
Expected: clean.

- [ ] **Step 6: Commit**

```bash
git add agent-verse-backend/app/api/governance.py agent-verse-backend/tests/api/test_governance_streams.py
git commit -m "feat(governance): add approvals SSE stream wrapping platform_events pubsub

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

### Task 3: Backend — `GET /governance/policies/stream` (SSE)

**Files:**
- Modify: `agent-verse-backend/app/api/governance.py` (after `stream_approvals`)
- Test: `agent-verse-backend/tests/api/test_governance_streams.py`

**Context:** Tails the `policy_changes` Redis channel (`policies.py:189`, published by `PolicyEngine.publish_change` on create/delete). The publisher does **not** scope the channel per-tenant, so filter messages by `tenant_id` inside the generator. Snapshot uses the existing list helper (`list_policies` logic, `governance.py:196`). When Redis is unavailable, emit snapshot then `stream_unavailable`.

**Interfaces:**
- Consumes: `_db_list_policies`/`_policy_registry` (existing), `app.state._policy_pubsub_redis`.
- Produces: `GET /governance/policies/stream` → `text/event-stream`; events: `policies_snapshot`, then `{type, tenant_id, action, ts}` policy-change events, optional `stream_unavailable`.

- [ ] **Step 1: Write the failing test**

```python
# append to tests/api/test_governance_streams.py
def test_policies_stream_emits_snapshot_without_redis() -> None:
    client = TestClient(_make_app(), raise_server_exceptions=False)
    with client.stream(
        "GET", "/governance/policies/stream", headers={"X-API-Key": _VALID_KEY}
    ) as resp:
        assert resp.status_code == 200
        assert "text/event-stream" in resp.headers["content-type"]
        body = ""
        for chunk in resp.iter_text():
            body += chunk
            if "stream_unavailable" in body:
                break
    assert "policies_snapshot" in body
    assert "stream_unavailable" in body
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd agent-verse-backend && uv run pytest tests/api/test_governance_streams.py -k policies_stream -x`
Expected: FAIL — 404.

- [ ] **Step 3: Implement the endpoint**

Add after `stream_approvals` in `governance.py`:

```python
@router.get("/policies/stream")
async def stream_policies(request: Request) -> StreamingResponse:
    """SSE stream of policy changes.

    Emits a `policies_snapshot`, then tails the `policy_changes` Redis channel
    (filtered to this tenant). When Redis is unavailable, emits the snapshot then
    `stream_unavailable`.
    """
    tenant_ctx: TenantContext = _require_tenant(request)
    db_policies = await _db_list_policies(request, tenant_ctx.tenant_id)
    if not db_policies:
        registry = _policy_registry(request)
        db_policies = list(registry.get(tenant_ctx.tenant_id, {}).values())
    redis = getattr(request.app.state, "_policy_pubsub_redis", None)

    async def gen() -> AsyncGenerator[str, None]:
        snapshot = {"type": "policies_snapshot", "policies": db_policies}
        yield f"data: {_json.dumps(snapshot)}\n\n"
        if redis is None:
            yield f'data: {_json.dumps({"type": "stream_unavailable"})}\n\n'
            return
        pubsub = redis.pubsub()
        await pubsub.subscribe("policy_changes")
        try:
            async for message in pubsub.listen():
                if message.get("type") != "message":
                    continue
                raw = message.get("data")
                try:
                    event = _json.loads(raw) if isinstance(raw, (str, bytes)) else raw
                except Exception:
                    continue
                if not isinstance(event, dict):
                    continue
                if event.get("tenant_id") != tenant_ctx.tenant_id:
                    continue
                out = {"type": "policy_changed", **event}
                yield f"data: {_json.dumps(out)}\n\n"
        finally:
            with __import__("contextlib").suppress(Exception):
                await pubsub.unsubscribe("policy_changes")
            with __import__("contextlib").suppress(Exception):
                await pubsub.close()

    return StreamingResponse(gen(), media_type="text/event-stream", headers=_SSE_HEADERS)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd agent-verse-backend && uv run pytest tests/api/test_governance_streams.py -k policies_stream -x`
Expected: PASS.

- [ ] **Step 5: Lint + type-check**

Run: `cd agent-verse-backend && uv run ruff check app/api/governance.py && uv run mypy app/api/governance.py`
Expected: clean.

- [ ] **Step 6: Commit**

```bash
git add agent-verse-backend/app/api/governance.py agent-verse-backend/tests/api/test_governance_streams.py
git commit -m "feat(governance): add policies SSE stream wrapping policy_changes pubsub

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

### Task 4: Backend — `DELETE /governance/notifications/{channel_id}` + `GET /governance/legal-holds`

**Files:**
- Modify: `agent-verse-backend/app/api/governance.py` (DELETE near `governance.py:553`; GET near `governance.py:836`)
- Test: `agent-verse-backend/tests/api/test_governance_streams.py`

**Context:** `NotificationService.remove_channel(channel_id, tenant_id)` exists (`notification_service.py:44`) but no route calls it. `legal_holds` table has POST only — the page needs a list. The GET reads the table directly (graceful empty when no DB), mirroring `_db_list_policies`.

**Interfaces:**
- Produces:
  - `DELETE /governance/notifications/{channel_id}` → 204 (404 if not found / no service).
  - `GET /governance/legal-holds` → `[{id, reason, expires_at, created_by, created_at}]`.

- [ ] **Step 1: Write the failing test**

```python
# append to tests/api/test_governance_streams.py
from app.services.notification_service import NotificationChannel, NotificationService


def _make_app_with_notifications() -> tuple[FastAPI, NotificationService]:
    app = _make_app()
    svc = NotificationService()
    app.state.notification_service = svc
    return app, svc


def test_delete_notification_channel() -> None:
    app, svc = _make_app_with_notifications()
    svc.add_channel(NotificationChannel(
        channel_id="c1", tenant_id=_CTX.tenant_id, channel_type="webhook", config={},
    ))
    client = TestClient(app, raise_server_exceptions=False)
    resp = client.delete("/governance/notifications/c1", headers={"X-API-Key": _VALID_KEY})
    assert resp.status_code == 204
    assert svc.get_channels(_CTX.tenant_id) == []


def test_delete_missing_notification_channel_404() -> None:
    app, _ = _make_app_with_notifications()
    client = TestClient(app, raise_server_exceptions=False)
    resp = client.delete("/governance/notifications/nope", headers={"X-API-Key": _VALID_KEY})
    assert resp.status_code == 404


def test_list_legal_holds_empty_without_db() -> None:
    client = TestClient(_make_app(), raise_server_exceptions=False)
    resp = client.get("/governance/legal-holds", headers={"X-API-Key": _VALID_KEY})
    assert resp.status_code == 200
    assert resp.json() == []
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd agent-verse-backend && uv run pytest tests/api/test_governance_streams.py -k "notification or legal_holds" -x`
Expected: FAIL — 404 (routes missing).

- [ ] **Step 3: Implement**

Add the DELETE route after `list_notification_channels` (`governance.py:553`):

```python
@router.delete("/notifications/{channel_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_notification_channel(request: Request, channel_id: str) -> None:
    tenant = _require_tenant(request)
    svc = getattr(request.app.state, "notification_service", None)
    if svc is None:
        raise HTTPException(404, "Notification channel not found")
    removed = svc.remove_channel(channel_id, tenant.tenant_id)
    if not removed:
        raise HTTPException(404, "Notification channel not found")
```

Add the GET route after `create_legal_hold` (end of file, `governance.py:836`):

```python
@router.get("/legal-holds")
async def list_legal_holds(request: Request) -> list[dict[str, Any]]:
    """List active legal holds for this tenant (empty when DB unavailable)."""
    ctx = _require_tenant(request)
    db = _get_db(request)
    if db is None:
        return []
    try:
        from sqlalchemy import text
        async with db() as session:
            rows = (await session.execute(text(
                "SELECT id, reason, expires_at, created_by "
                "FROM legal_holds WHERE tenant_id = :tid ORDER BY id"
            ), {"tid": ctx.tenant_id})).fetchall()
        return [
            {
                "id": r[0],
                "reason": r[1],
                "expires_at": r[2].isoformat() if r[2] else None,
                "created_by": r[3],
            }
            for r in rows
        ]
    except Exception:
        return []
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd agent-verse-backend && uv run pytest tests/api/test_governance_streams.py -x`
Expected: PASS (all tests in the file).

- [ ] **Step 5: Lint + type-check**

Run: `cd agent-verse-backend && uv run ruff check app/api/governance.py && uv run mypy app/api/governance.py`
Expected: clean.

- [ ] **Step 6: Commit**

```bash
git add agent-verse-backend/app/api/governance.py agent-verse-backend/tests/api/test_governance_streams.py
git commit -m "feat(governance): add channel delete + legal-hold list routes

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

### Task 5: Client — `notificationsApi`, `auditApi`, `rbacApi` + types + stream-path helpers

**Files:**
- Modify: `src/lib/api/client.ts`
- Test: `src/lib/api/governance-realtime.test.ts` (create)

**Interfaces:**
- Produces (all under `@/lib/api/client`):
  - Types: `AuditEvent`, `AuditQuery`, `NotificationChannel`, `CreateNotificationChannelRequest`, `RoleAssignment`, `IpAllowlistEntry`, `LegalHold`, `GdprExportJob`, `ConsentRecord`.
  - `notificationsApi.list()` → `NotificationChannel[]` (`GET /governance/notifications`)
  - `notificationsApi.create(body)` → `{ channel_id: string; type: string; status: string }` (`POST /governance/notifications`)
  - `notificationsApi.delete(channelId)` → `void` (`DELETE /governance/notifications/{id}`)
  - `auditApi.query(q?: AuditQuery)` → `AuditEvent[]` (`GET /governance/audit` with query string)
  - `rbacApi.listRoles()` → `RoleAssignment[]` (`GET /tenants/me/roles`)
  - `rbacApi.createRole(userId, role)` → `RoleAssignment` (`POST /tenants/me/roles`)
  - `rbacApi.deleteRole(roleId)` → `void` (`DELETE /tenants/me/roles/{id}`)
  - `rbacApi.listIpAllowlist()` → `IpAllowlistEntry[]` (`GET /tenants/me/ip-allowlist`)
  - `rbacApi.addIpAllowlist(cidr, description?)` → `IpAllowlistEntry` (`POST /tenants/me/ip-allowlist`)
  - `rbacApi.deleteIpAllowlist(entryId)` → `void` (`DELETE /tenants/me/ip-allowlist/{id}`)
  - `complianceApi.listLegalHolds()` → `LegalHold[]` (`GET /governance/legal-holds`)
  - `complianceApi.startGdprExport()` → `{ job_id: string; status: string; poll_url: string }` (`POST /compliance/export/start`)
  - `complianceApi.getGdprExportStatus(jobId)` → `GdprExportJob` (`GET /compliance/export/jobs/{jobId}`)
  - `complianceApi.recordConsent(purpose, legalBasis?)` → `{ consent_id: string; purpose: string; status: string }` (`POST /compliance/consent`)
  - `complianceApi.revokeConsent(purpose)` → `{ purpose: string; status: string }` (`DELETE /compliance/consent/{purpose}`)
  - `governanceApi.approvalsStreamPath()` → `"/governance/approvals/stream"`; `governanceApi.policiesStreamPath()` → `"/governance/policies/stream"`.

- [ ] **Step 1: Write the failing test**

```ts
// src/lib/api/governance-realtime.test.ts
import { afterEach, expect, test, vi } from 'vitest';
import { notificationsApi, auditApi, rbacApi, complianceApi } from '@/lib/api/client';

afterEach(() => vi.restoreAllMocks());

function mockOk(body: unknown, status = 200) {
  return vi.spyOn(globalThis, 'fetch').mockResolvedValue(
    new Response(status === 204 ? null : JSON.stringify(body), {
      status, headers: { 'Content-Type': 'application/json' },
    }),
  );
}

test('auditApi.query builds the governance/audit query string', async () => {
  const f = mockOk([]);
  await auditApi.query({ tool_name: 'jira.delete', limit: 50, start_time: '2026-01-01' });
  const url = String(f.mock.calls[0][0]);
  expect(url).toContain('/governance/audit');
  expect(url).toContain('tool_name=jira.delete');
  expect(url).toContain('limit=50');
  expect(url).toContain('start_time=2026-01-01');
});

test('notificationsApi.create posts channel_type+config', async () => {
  const f = mockOk({ channel_id: 'c1', type: 'webhook', status: 'created' }, 201);
  await notificationsApi.create({ channel_type: 'webhook', config: { url: 'https://x' } });
  const [url, init] = f.mock.calls[0];
  expect(String(url)).toContain('/governance/notifications');
  expect(JSON.parse(String((init as RequestInit).body))).toEqual({
    channel_type: 'webhook', config: { url: 'https://x' },
  });
});

test('rbacApi.createRole posts user_id+role', async () => {
  const f = mockOk({ id: 'r1', user_id: 'u1', role: 'approver', tenant_id: 't' }, 201);
  await rbacApi.createRole('u1', 'approver');
  const init = f.mock.calls[0][1] as RequestInit;
  expect(JSON.parse(String(init.body))).toEqual({ user_id: 'u1', role: 'approver' });
});

test('rbacApi.addIpAllowlist posts cidr+description', async () => {
  const f = mockOk({ id: 'e1', cidr: '10.0.0.0/8', description: 'office' }, 201);
  await rbacApi.addIpAllowlist('10.0.0.0/8', 'office');
  const url = String(f.mock.calls[0][0]);
  expect(url).toContain('/tenants/me/ip-allowlist');
});

test('complianceApi.getGdprExportStatus hits compliance/export/jobs/{id}', async () => {
  const f = mockOk({ job_id: 'j1', status: 'pending', completed_at: null, download_url: null, error: null });
  await complianceApi.getGdprExportStatus('j1');
  expect(String(f.mock.calls[0][0])).toContain('/compliance/export/jobs/j1');
});
```

- [ ] **Step 2: Run test to verify it fails**

Run: `npm run test -- src/lib/api/governance-realtime.test.ts`
Expected: FAIL — `notificationsApi`/`auditApi`/`rbacApi`/`complianceApi` undefined.

- [ ] **Step 3: Implement in `src/lib/api/client.ts`**

Append to the file (after the `memoryApi` block at `client.ts:454`):

```ts
// ── Governance real-time helpers + Audit ──────────────────────────────────────

export interface AuditEvent {
  event_id: string;
  goal_id: string;
  tool_name: string;
  action_level: string;
  outcome: string;
  step_id?: string;
  approver?: string;
  note?: string;
}

export interface AuditQuery {
  goal_id?: string;
  tool_name?: string;
  limit?: number;
  offset?: number;
  start_time?: string;
  end_time?: string;
}

export const auditApi = {
  query: (q: AuditQuery = {}) => {
    const params = new URLSearchParams();
    if (q.goal_id) params.set("goal_id", q.goal_id);
    if (q.tool_name) params.set("tool_name", q.tool_name);
    params.set("limit", String(q.limit ?? 200));
    if (q.offset) params.set("offset", String(q.offset));
    if (q.start_time) params.set("start_time", q.start_time);
    if (q.end_time) params.set("end_time", q.end_time);
    return request<AuditEvent[]>(`/governance/audit?${params.toString()}`);
  },
};

// ── Notifications ──────────────────────────────────────────────────────────────

export interface NotificationChannel {
  channel_id: string;
  type: string;
  enabled: boolean;
}

export interface CreateNotificationChannelRequest {
  channel_type: string; // "slack" | "webhook" | "teams"
  config: Record<string, unknown>;
}

export const notificationsApi = {
  list: () => request<NotificationChannel[]>("/governance/notifications"),
  create: (body: CreateNotificationChannelRequest) =>
    request<{ channel_id: string; type: string; status: string }>(
      "/governance/notifications",
      { method: "POST", body: JSON.stringify(body) },
    ),
  delete: (channelId: string) =>
    request<void>(`/governance/notifications/${channelId}`, { method: "DELETE" }),
};

// ── RBAC: roles + IP allowlist ─────────────────────────────────────────────────

export interface RoleAssignment {
  id: string;
  user_id: string;
  role: string;
  created_at?: string;
}

export interface IpAllowlistEntry {
  id: string;
  cidr: string;
  description: string;
  created_at?: string;
}

export const rbacApi = {
  listRoles: () => request<RoleAssignment[]>("/tenants/me/roles"),
  createRole: (userId: string, role: string) =>
    request<RoleAssignment>("/tenants/me/roles", {
      method: "POST",
      body: JSON.stringify({ user_id: userId, role }),
    }),
  deleteRole: (roleId: string) =>
    request<void>(`/tenants/me/roles/${roleId}`, { method: "DELETE" }),
  listIpAllowlist: () => request<IpAllowlistEntry[]>("/tenants/me/ip-allowlist"),
  addIpAllowlist: (cidr: string, description = "") =>
    request<IpAllowlistEntry>("/tenants/me/ip-allowlist", {
      method: "POST",
      body: JSON.stringify({ cidr, description }),
    }),
  deleteIpAllowlist: (entryId: string) =>
    request<void>(`/tenants/me/ip-allowlist/${entryId}`, { method: "DELETE" }),
};

// ── Compliance: legal hold + GDPR export + consent ─────────────────────────────

export interface LegalHold {
  id: string;
  reason: string;
  expires_at: string | null;
  created_by: string;
}

export interface GdprExportJob {
  job_id: string;
  status: string; // "pending" | "running" | "complete" | "failed"
  completed_at: string | null;
  download_url: string | null;
  error: string | null;
}

export const complianceApi = {
  listLegalHolds: () => request<LegalHold[]>("/governance/legal-holds"),
  startGdprExport: () =>
    request<{ job_id: string; status: string; poll_url: string }>(
      "/compliance/export/start",
      { method: "POST" },
    ),
  getGdprExportStatus: (jobId: string) =>
    request<GdprExportJob>(`/compliance/export/jobs/${jobId}`),
  recordConsent: (purpose: string, legalBasis = "legitimate_interest") =>
    request<{ consent_id: string; purpose: string; status: string }>(
      "/compliance/consent",
      { method: "POST", body: JSON.stringify({ purpose, legal_basis: legalBasis }) },
    ),
  revokeConsent: (purpose: string) =>
    request<{ purpose: string; status: string }>(
      `/compliance/consent/${purpose}`,
      { method: "DELETE" },
    ),
};
```

Add the stream-path helpers to the existing `governanceApi` object (`client.ts:298-316`):

```ts
  approvalsStreamPath: () => "/governance/approvals/stream",
  policiesStreamPath: () => "/governance/policies/stream",
```

- [ ] **Step 4: Run test + typecheck**

Run: `npm run test -- src/lib/api/governance-realtime.test.ts && npm run typecheck`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/lib/api/client.ts src/lib/api/governance-realtime.test.ts
git commit -m "feat(client): add notificationsApi, auditApi, rbacApi, complianceApi + stream paths

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

### Task 6: Typed Audit Explorer with filters + CSV/JSON export

**Files:**
- Modify: `src/features/audit/AuditExplorerPage.tsx`
- Test: `src/features/audit/AuditExplorerPage.test.tsx` (create)

**Context:** Replace the `any[]` raw-fetch stub (`AuditExplorerPage.tsx:6,16-26`) with the typed `auditApi.query` + TanStack Query. Add server-side filters (date range → `start_time`/`end_time`, `tool_name`, plus a client-side `outcome` filter), a free-text filter, and CSV + JSON export buttons. Reuse `Skeleton`/`EmptyState`/`StatusBadge`.

**Interfaces:**
- Consumes: `auditApi.query` (Task 5), `AuditEvent`, `Skeleton`, `EmptyState`, `StatusBadge`, `toast`.
- Produces: none.

- [ ] **Step 1: Write the failing test**

```tsx
// src/features/audit/AuditExplorerPage.test.tsx
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter } from 'react-router-dom';
import { beforeEach, expect, test, vi } from 'vitest';
import { useAuthStore } from '@/stores/auth';
import AuditExplorerPage from './AuditExplorerPage';

function renderPage() {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={qc}>
      <MemoryRouter><AuditExplorerPage /></MemoryRouter>
    </QueryClientProvider>,
  );
}

beforeEach(() => {
  sessionStorage.clear(); localStorage.clear();
  useAuthStore.setState({ apiKey: 'k', tenantId: 't', plan: 'free', isAuthenticated: true });
});

const SAMPLE = [
  { event_id: 'e1', goal_id: 'g1', tool_name: 'jira.delete', action_level: 'deny', outcome: 'denied', note: '' },
  { event_id: 'e2', goal_id: 'g2', tool_name: 'github.read', action_level: 'allow', outcome: 'success', note: '' },
];

test('renders typed audit rows from auditApi', async () => {
  vi.spyOn(globalThis, 'fetch').mockResolvedValue(
    new Response(JSON.stringify(SAMPLE), { status: 200, headers: { 'Content-Type': 'application/json' } }),
  );
  renderPage();
  expect(await screen.findByText('jira.delete')).toBeInTheDocument();
  expect(screen.getByText('github.read')).toBeInTheDocument();
});

test('tool filter is forwarded as a query param', async () => {
  const f = vi.spyOn(globalThis, 'fetch').mockResolvedValue(
    new Response(JSON.stringify(SAMPLE), { status: 200, headers: { 'Content-Type': 'application/json' } }),
  );
  renderPage();
  await screen.findByText('jira.delete');
  await userEvent.type(screen.getByLabelText(/tool name/i), 'jira.delete');
  await userEvent.click(screen.getByRole('button', { name: /apply filters/i }));
  await waitFor(() =>
    expect(f.mock.calls.some(([u]) => String(u).includes('tool_name=jira.delete'))).toBe(true),
  );
});

test('export JSON button is present once rows load', async () => {
  vi.spyOn(globalThis, 'fetch').mockResolvedValue(
    new Response(JSON.stringify(SAMPLE), { status: 200, headers: { 'Content-Type': 'application/json' } }),
  );
  renderPage();
  await screen.findByText('jira.delete');
  expect(screen.getByRole('button', { name: /export json/i })).toBeInTheDocument();
  expect(screen.getByRole('button', { name: /export csv/i })).toBeInTheDocument();
});
```

- [ ] **Step 2: Run test to verify it fails**

Run: `npm run test -- src/features/audit/AuditExplorerPage.test.tsx`
Expected: FAIL — current page uses raw fetch + has no labelled tool input / export buttons.

- [ ] **Step 3: Implement the page**

Replace the entire contents of `src/features/audit/AuditExplorerPage.tsx`:

```tsx
import { useMemo, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { Download, RefreshCw } from "lucide-react";
import { auditApi, AuditEvent, AuditQuery } from "@/lib/api/client";
import { Skeleton } from "@/components/ui/Skeleton";
import { EmptyState } from "@/components/ui/EmptyState";
import { StatusBadge } from "@/components/ui/StatusBadge";
import { toast } from "@/stores/toast";

const CSV_COLUMNS: (keyof AuditEvent)[] = [
  "event_id", "goal_id", "tool_name", "action_level", "outcome", "approver", "note",
];

function toCsv(rows: AuditEvent[]): string {
  const header = CSV_COLUMNS.join(",");
  const body = rows
    .map((r) =>
      CSV_COLUMNS
        .map((c) => `"${String(r[c] ?? "").replace(/"/g, '""')}"`)
        .join(","),
    )
    .join("\n");
  return `${header}\n${body}`;
}

function download(filename: string, content: string, mime: string): void {
  const blob = new Blob([content], { type: mime });
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = filename;
  a.click();
  URL.revokeObjectURL(url);
}

export function AuditExplorerPage() {
  const [applied, setApplied] = useState<AuditQuery>({ limit: 200 });
  const [toolName, setToolName] = useState("");
  const [startTime, setStartTime] = useState("");
  const [endTime, setEndTime] = useState("");
  const [outcome, setOutcome] = useState("");
  const [text, setText] = useState("");

  const { data: entries = [], isLoading, error, refetch, isFetching } = useQuery({
    queryKey: ["audit", applied],
    queryFn: () => auditApi.query(applied),
  });

  const filtered = useMemo(
    () =>
      entries.filter((e) => {
        if (outcome && !(e.outcome ?? "").toLowerCase().includes(outcome.toLowerCase())) return false;
        if (text && !JSON.stringify(e).toLowerCase().includes(text.toLowerCase())) return false;
        return true;
      }),
    [entries, outcome, text],
  );

  function applyFilters() {
    setApplied({
      limit: 200,
      tool_name: toolName || undefined,
      start_time: startTime || undefined,
      end_time: endTime || undefined,
    });
  }

  function exportJson() {
    if (!filtered.length) return toast({ kind: "info", message: "Nothing to export." });
    download("audit-events.json", JSON.stringify(filtered, null, 2), "application/json");
  }

  function exportCsv() {
    if (!filtered.length) return toast({ kind: "info", message: "Nothing to export." });
    download("audit-events.csv", toCsv(filtered), "text/csv");
  }

  return (
    <div className="p-6 max-w-6xl mx-auto space-y-6">
      <div className="flex items-center justify-between">
        <h1 className="text-2xl font-bold">Audit Explorer</h1>
        <div className="flex items-center gap-2">
          <button
            onClick={() => void refetch()}
            className="inline-flex items-center gap-1.5 px-3 py-1.5 bg-muted rounded-md text-sm hover:bg-accent"
          >
            <RefreshCw className={`h-4 w-4 ${isFetching ? "animate-spin" : ""}`} aria-hidden="true" />
            Refresh
          </button>
          <button
            onClick={exportCsv}
            className="inline-flex items-center gap-1.5 px-3 py-1.5 bg-muted rounded-md text-sm hover:bg-accent"
          >
            <Download className="h-4 w-4" aria-hidden="true" /> Export CSV
          </button>
          <button
            onClick={exportJson}
            className="inline-flex items-center gap-1.5 px-3 py-1.5 bg-muted rounded-md text-sm hover:bg-accent"
          >
            <Download className="h-4 w-4" aria-hidden="true" /> Export JSON
          </button>
        </div>
      </div>

      <div className="grid grid-cols-1 md:grid-cols-5 gap-3 bg-card border border-border rounded-xl p-4">
        <label className="flex flex-col gap-1 text-xs text-muted-foreground">
          Tool name
          <input
            value={toolName}
            onChange={(e) => setToolName(e.target.value)}
            className="px-2 py-1.5 border border-input rounded-md bg-background text-sm"
          />
        </label>
        <label className="flex flex-col gap-1 text-xs text-muted-foreground">
          Start time
          <input
            type="datetime-local"
            value={startTime}
            onChange={(e) => setStartTime(e.target.value)}
            className="px-2 py-1.5 border border-input rounded-md bg-background text-sm"
          />
        </label>
        <label className="flex flex-col gap-1 text-xs text-muted-foreground">
          End time
          <input
            type="datetime-local"
            value={endTime}
            onChange={(e) => setEndTime(e.target.value)}
            className="px-2 py-1.5 border border-input rounded-md bg-background text-sm"
          />
        </label>
        <label className="flex flex-col gap-1 text-xs text-muted-foreground">
          Outcome
          <input
            value={outcome}
            onChange={(e) => setOutcome(e.target.value)}
            placeholder="success / denied / fail"
            className="px-2 py-1.5 border border-input rounded-md bg-background text-sm"
          />
        </label>
        <div className="flex items-end">
          <button
            onClick={applyFilters}
            className="w-full px-3 py-1.5 bg-primary text-primary-foreground rounded-md text-sm hover:opacity-90"
          >
            Apply filters
          </button>
        </div>
      </div>

      <input
        value={text}
        onChange={(e) => setText(e.target.value)}
        placeholder="Free-text filter (client-side)…"
        className="w-full px-3 py-2 border border-input rounded-md bg-background text-sm"
      />

      {isLoading ? (
        <div className="space-y-2">
          {Array.from({ length: 6 }).map((_, i) => <Skeleton key={i} className="h-10 w-full" />)}
        </div>
      ) : error ? (
        <div className="text-sm text-destructive">Failed to load audit log: {String(error)}</div>
      ) : filtered.length === 0 ? (
        <EmptyState title="No audit entries" description="No events match the current filters." />
      ) : (
        <div className="overflow-x-auto border border-border rounded-xl">
          <table className="w-full text-sm">
            <thead>
              <tr className="bg-muted text-left">
                <th className="px-3 py-2 font-medium">Tool</th>
                <th className="px-3 py-2 font-medium">Action</th>
                <th className="px-3 py-2 font-medium">Outcome</th>
                <th className="px-3 py-2 font-medium">Goal</th>
                <th className="px-3 py-2 font-medium">Approver</th>
              </tr>
            </thead>
            <tbody>
              {filtered.slice(0, 200).map((e) => (
                <tr key={e.event_id} className="border-t border-border">
                  <td className="px-3 py-2 font-mono text-xs">{e.tool_name || "—"}</td>
                  <td className="px-3 py-2"><StatusBadge status={e.action_level} /></td>
                  <td className="px-3 py-2">{e.outcome || "—"}</td>
                  <td className="px-3 py-2 font-mono text-xs text-muted-foreground">{e.goal_id?.slice(0, 16)}</td>
                  <td className="px-3 py-2 text-xs">{e.approver || "—"}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}

export default AuditExplorerPage;
```

- [ ] **Step 4: Run test + typecheck**

Run: `npm run test -- src/features/audit/AuditExplorerPage.test.tsx && npm run typecheck`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/features/audit/AuditExplorerPage.tsx src/features/audit/AuditExplorerPage.test.tsx
git commit -m "feat(audit): typed audit explorer with filters and CSV/JSON export

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

### Task 7: Real-time approvals — auto-update ApprovalsPage via `useEventStream`

**Files:**
- Modify: `src/features/approvals/ApprovalsPage.tsx`
- Test: `src/features/approvals/ApprovalsPage.test.tsx` (create if absent; otherwise extend)

**Context:** The page already polls every 5s (`ApprovalsPage.tsx:23`). Add `useEventStream(governanceApi.approvalsStreamPath())` and, on any forwarded event, invalidate the `["approvals"]` query for instant updates; relax the poll to a 30s safety net so the stream is the primary update path.

**Interfaces:**
- Consumes: `useEventStream` (Task 1), `governanceApi.approvalsStreamPath` (Task 5), `useQueryClient`.
- Produces: none.

- [ ] **Step 1: Write the failing test**

```tsx
// src/features/approvals/ApprovalsPage.test.tsx
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { render, screen, waitFor } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { beforeEach, expect, test, vi } from 'vitest';
import { useAuthStore } from '@/stores/auth';
import { ApprovalsPage } from './ApprovalsPage';

let invalidateSpy: ReturnType<typeof vi.fn>;

vi.mock('@/lib/sse/useEventStream', () => ({
  useEventStream: (_path: string | null, opts?: { onEvent?: (e: { type: string }) => void }) => {
    // Simulate a pushed event once on mount.
    setTimeout(() => opts?.onEvent?.({ type: 'waiting_approval' }), 0);
    return { events: [], connected: true };
  },
}));

function renderPage() {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  invalidateSpy = vi.spyOn(qc, 'invalidateQueries') as unknown as ReturnType<typeof vi.fn>;
  return render(
    <QueryClientProvider client={qc}>
      <MemoryRouter><ApprovalsPage /></MemoryRouter>
    </QueryClientProvider>,
  );
}

beforeEach(() => {
  sessionStorage.clear(); localStorage.clear();
  useAuthStore.setState({ apiKey: 'k', tenantId: 't', plan: 'free', isAuthenticated: true });
  vi.spyOn(globalThis, 'fetch').mockResolvedValue(
    new Response(JSON.stringify([]), { status: 200, headers: { 'Content-Type': 'application/json' } }),
  );
});

test('invalidates approvals query when a stream event arrives', async () => {
  renderPage();
  await waitFor(() =>
    expect(invalidateSpy).toHaveBeenCalledWith(expect.objectContaining({ queryKey: ['approvals'] })),
  );
});
```

- [ ] **Step 2: Run test to verify it fails**

Run: `npm run test -- src/features/approvals/ApprovalsPage.test.tsx`
Expected: FAIL — the page does not consume `useEventStream` yet.

- [ ] **Step 3: Implement**

In `src/features/approvals/ApprovalsPage.tsx`, add imports:

```tsx
import { useEventStream } from "@/lib/sse/useEventStream";
import { governanceApi } from "@/lib/api/client";
```

Inside the component, after the `useQuery` block (`ApprovalsPage.tsx:24`), change the poll interval to 30s and wire the stream:

```tsx
  // useQuery options change:
  //   refetchInterval: 30_000,   // safety net; stream is primary

  useEventStream(governanceApi.approvalsStreamPath(), {
    onEvent: () => qc.invalidateQueries({ queryKey: ["approvals"] }),
  });
```

(Update the existing `refetchInterval: 5_000` on the `useQuery` to `30_000`, and the "Auto-refreshes every 5s" caption to "Live updates".)

- [ ] **Step 4: Run test + typecheck**

Run: `npm run test -- src/features/approvals/ApprovalsPage.test.tsx && npm run typecheck`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/features/approvals/ApprovalsPage.tsx src/features/approvals/ApprovalsPage.test.tsx
git commit -m "feat(approvals): live auto-update via useEventStream

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

### Task 8: Live pending-approvals counter in the TopBar

**Files:**
- Create: `src/components/ui/PendingApprovalsBadge.tsx`, `src/components/ui/PendingApprovalsBadge.test.tsx`
- Modify: `src/components/ui/TopBar.tsx`

**Context:** Mirror the Sidebar's approvals badge (`Sidebar.tsx:31-37,61`) but live: query `governanceApi.listApprovals()` and invalidate it on each `useEventStream` event so the count updates in real time. Render a small clickable badge that navigates to `/approvals`.

**Interfaces:**
- Consumes: `governanceApi.listApprovals` + `approvalsStreamPath`, `useEventStream`, `useNavigate`.
- Produces: `PendingApprovalsBadge()` component.

- [ ] **Step 1: Write the failing test**

```tsx
// src/components/ui/PendingApprovalsBadge.test.tsx
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { render, screen } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { beforeEach, expect, test, vi } from 'vitest';
import { useAuthStore } from '@/stores/auth';
import { PendingApprovalsBadge } from './PendingApprovalsBadge';

vi.mock('@/lib/sse/useEventStream', () => ({
  useEventStream: () => ({ events: [], connected: true }),
}));

function renderBadge() {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={qc}>
      <MemoryRouter><PendingApprovalsBadge /></MemoryRouter>
    </QueryClientProvider>,
  );
}

beforeEach(() => {
  sessionStorage.clear(); localStorage.clear();
  useAuthStore.setState({ apiKey: 'k', tenantId: 't', plan: 'free', isAuthenticated: true });
});

test('shows the pending count', async () => {
  vi.spyOn(globalThis, 'fetch').mockResolvedValue(
    new Response(JSON.stringify([
      { request_id: 'r1', goal_id: 'g1', status: 'pending' },
      { request_id: 'r2', goal_id: 'g2', status: 'pending' },
      { request_id: 'r3', goal_id: 'g3', status: 'approved' },
    ]), { status: 200, headers: { 'Content-Type': 'application/json' } }),
  );
  renderBadge();
  expect(await screen.findByText('2')).toBeInTheDocument();
});

test('renders nothing when there are no pending approvals', async () => {
  vi.spyOn(globalThis, 'fetch').mockResolvedValue(
    new Response(JSON.stringify([]), { status: 200, headers: { 'Content-Type': 'application/json' } }),
  );
  const { container } = renderBadge();
  // Wait a tick for the query to resolve, then assert no badge button.
  await new Promise((r) => setTimeout(r, 10));
  expect(container.querySelector('[aria-label="Pending approvals"]')).toBeNull();
});
```

- [ ] **Step 2: Run test to verify it fails**

Run: `npm run test -- src/components/ui/PendingApprovalsBadge.test.tsx`
Expected: FAIL — module not found.

- [ ] **Step 3: Implement the badge**

```tsx
// src/components/ui/PendingApprovalsBadge.tsx
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useNavigate } from "react-router-dom";
import { Bell } from "lucide-react";
import { governanceApi } from "@/lib/api/client";
import { useEventStream } from "@/lib/sse/useEventStream";

export function PendingApprovalsBadge() {
  const qc = useQueryClient();
  const navigate = useNavigate();
  const { data: approvals = [] } = useQuery({
    queryKey: ["approvals"],
    queryFn: () => governanceApi.listApprovals(),
    refetchInterval: 30_000,
  });

  useEventStream(governanceApi.approvalsStreamPath(), {
    onEvent: () => qc.invalidateQueries({ queryKey: ["approvals"] }),
  });

  const pending = approvals.filter((a) => a.status === "pending").length;
  if (pending === 0) return null;

  return (
    <button
      onClick={() => navigate("/approvals")}
      aria-label="Pending approvals"
      className="relative p-1.5 rounded-md hover:bg-accent transition-colors text-muted-foreground"
    >
      <Bell className="h-4 w-4" aria-hidden="true" />
      <span className="absolute -top-0.5 -right-0.5 inline-flex items-center justify-center min-w-[1.1rem] h-[1.1rem] px-1 rounded-full text-[10px] font-bold bg-orange-500 text-white">
        {pending}
      </span>
    </button>
  );
}
```

- [ ] **Step 4: Mount it in `TopBar.tsx`**

In `src/components/ui/TopBar.tsx`, import and render the badge in the right-hand control group (after the tenant id `<span>`, `TopBar.tsx:40`):

```tsx
import { PendingApprovalsBadge } from "@/components/ui/PendingApprovalsBadge";
// ... inside the right-hand <div className="flex items-center gap-3">, before the theme toggle:
        <PendingApprovalsBadge />
```

- [ ] **Step 5: Run tests + typecheck**

Run: `npm run test -- src/components/ui/PendingApprovalsBadge.test.tsx && npm run typecheck`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add src/components/ui/PendingApprovalsBadge.tsx src/components/ui/PendingApprovalsBadge.test.tsx src/components/ui/TopBar.tsx
git commit -m "feat(topbar): live pending-approvals badge driven by SSE

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

### Task 9: Notification Center page (channels list + create + delete)

**Files:**
- Create: `src/features/notifications/NotificationCenterPage.tsx`, `NotificationCenterPage.test.tsx`
- Modify: `src/app/App.tsx`, `src/components/ui/Sidebar.tsx`

**Context:** List channels (`notificationsApi.list`), create (`channel_type` ∈ slack/webhook/teams + JSON `config`), delete. Delivery logs have **no backend endpoint** — render a clearly-labelled "Delivery logs are not yet available" panel (do not fabricate data).

**Interfaces:**
- Consumes: `notificationsApi.{list,create,delete}` (Task 5), `Skeleton`/`EmptyState`, `toast`.
- Produces: `NotificationCenterPage()`; route `/notifications`.

- [ ] **Step 1: Write the failing test**

```tsx
// src/features/notifications/NotificationCenterPage.test.tsx
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter } from 'react-router-dom';
import { beforeEach, expect, test, vi } from 'vitest';
import { useAuthStore } from '@/stores/auth';
import { NotificationCenterPage } from './NotificationCenterPage';

function renderPage() {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={qc}>
      <MemoryRouter><NotificationCenterPage /></MemoryRouter>
    </QueryClientProvider>,
  );
}

beforeEach(() => {
  sessionStorage.clear(); localStorage.clear();
  useAuthStore.setState({ apiKey: 'k', tenantId: 't', plan: 'free', isAuthenticated: true });
});

test('lists existing channels', async () => {
  vi.spyOn(globalThis, 'fetch').mockResolvedValue(
    new Response(JSON.stringify([{ channel_id: 'c1', type: 'slack', enabled: true }]),
      { status: 200, headers: { 'Content-Type': 'application/json' } }),
  );
  renderPage();
  expect(await screen.findByText(/slack/i)).toBeInTheDocument();
});

test('create channel posts channel_type', async () => {
  const f = vi.spyOn(globalThis, 'fetch').mockImplementation(async (input, init) => {
    const url = String(input);
    if (url.includes('/governance/notifications') && init?.method === 'POST')
      return new Response(JSON.stringify({ channel_id: 'c2', type: 'webhook', status: 'created' }), { status: 201 });
    return new Response(JSON.stringify([]), { status: 200, headers: { 'Content-Type': 'application/json' } });
  });
  renderPage();
  await screen.findByRole('button', { name: /add channel/i });
  await userEvent.click(screen.getByRole('button', { name: /add channel/i }));
  await waitFor(() =>
    expect(f.mock.calls.some(([u, i]) =>
      String(u).includes('/governance/notifications') && (i as RequestInit)?.method === 'POST')).toBe(true),
  );
});
```

- [ ] **Step 2: Run test to verify it fails**

Run: `npm run test -- src/features/notifications/NotificationCenterPage.test.tsx`
Expected: FAIL — module not found.

- [ ] **Step 3: Implement the page**

```tsx
// src/features/notifications/NotificationCenterPage.tsx
import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Trash2 } from "lucide-react";
import { notificationsApi } from "@/lib/api/client";
import { Skeleton } from "@/components/ui/Skeleton";
import { EmptyState } from "@/components/ui/EmptyState";
import { toast } from "@/stores/toast";

const CHANNEL_TYPES = ["slack", "webhook", "teams"] as const;

export function NotificationCenterPage() {
  const qc = useQueryClient();
  const [channelType, setChannelType] = useState<(typeof CHANNEL_TYPES)[number]>("webhook");
  const [configText, setConfigText] = useState('{ "url": "" }');

  const { data: channels = [], isLoading, error } = useQuery({
    queryKey: ["notification-channels"],
    queryFn: () => notificationsApi.list(),
  });

  const createMutation = useMutation({
    mutationFn: () => {
      let config: Record<string, unknown> = {};
      try {
        config = JSON.parse(configText) as Record<string, unknown>;
      } catch {
        throw new Error("Config must be valid JSON");
      }
      return notificationsApi.create({ channel_type: channelType, config });
    },
    onSuccess: () => {
      toast({ kind: "success", message: "Channel created." });
      qc.invalidateQueries({ queryKey: ["notification-channels"] });
    },
    onError: (e) => toast({ kind: "error", message: String(e) }),
  });

  const deleteMutation = useMutation({
    mutationFn: (id: string) => notificationsApi.delete(id),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["notification-channels"] }),
    onError: (e) => toast({ kind: "error", message: String(e) }),
  });

  return (
    <div className="p-6 max-w-4xl mx-auto space-y-6">
      <div>
        <h1 className="text-2xl font-bold">Notification Center</h1>
        <p className="text-muted-foreground text-sm mt-1">
          Configure where approval requests and goal updates are delivered.
        </p>
      </div>

      <div className="bg-card border border-border rounded-xl p-4 space-y-3">
        <h2 className="font-semibold text-sm">Add a channel</h2>
        <div className="flex flex-wrap gap-3 items-end">
          <label className="flex flex-col gap-1 text-xs text-muted-foreground">
            Type
            <select
              value={channelType}
              onChange={(e) => setChannelType(e.target.value as (typeof CHANNEL_TYPES)[number])}
              className="px-2 py-1.5 border border-input rounded-md bg-background text-sm"
            >
              {CHANNEL_TYPES.map((t) => <option key={t} value={t}>{t}</option>)}
            </select>
          </label>
          <label className="flex flex-col gap-1 text-xs text-muted-foreground flex-1 min-w-[16rem]">
            Config (JSON)
            <input
              value={configText}
              onChange={(e) => setConfigText(e.target.value)}
              className="px-2 py-1.5 border border-input rounded-md bg-background text-sm font-mono"
            />
          </label>
          <button
            onClick={() => createMutation.mutate()}
            disabled={createMutation.isPending}
            className="px-4 py-2 bg-primary text-primary-foreground rounded-md text-sm hover:opacity-90 disabled:opacity-50"
          >
            Add channel
          </button>
        </div>
      </div>

      {isLoading ? (
        <div className="space-y-2">
          {Array.from({ length: 3 }).map((_, i) => <Skeleton key={i} className="h-12 w-full" />)}
        </div>
      ) : error ? (
        <div className="text-sm text-destructive">Failed to load channels: {String(error)}</div>
      ) : channels.length === 0 ? (
        <EmptyState title="No channels" description="Add a Slack, webhook, or Teams channel to receive alerts." />
      ) : (
        <div className="space-y-2">
          {channels.map((c) => (
            <div key={c.channel_id} className="flex items-center justify-between bg-card border border-border rounded-lg px-4 py-3">
              <div className="text-sm">
                <span className="font-medium">{c.type}</span>
                <span className="ml-2 font-mono text-xs text-muted-foreground">{c.channel_id}</span>
                {!c.enabled && <span className="ml-2 text-xs text-muted-foreground">(disabled)</span>}
              </div>
              <button
                onClick={() => deleteMutation.mutate(c.channel_id)}
                aria-label={`Delete channel ${c.channel_id}`}
                className="p-1.5 rounded-md hover:bg-accent text-muted-foreground"
              >
                <Trash2 className="h-4 w-4" aria-hidden="true" />
              </button>
            </div>
          ))}
        </div>
      )}

      <div className="bg-muted/40 border border-border rounded-xl p-4 text-sm text-muted-foreground">
        Delivery logs are not yet available. Channel delivery history will appear here once the
        backend records per-channel delivery events.
      </div>
    </div>
  );
}

export default NotificationCenterPage;
```

- [ ] **Step 4: Wire route + nav**

In `src/app/App.tsx`, add the import and route (alongside the other feature routes, near `App.tsx:104`):

```tsx
import { NotificationCenterPage } from "@/features/notifications/NotificationCenterPage";
// ...
        <Route path="notifications" element={<NotificationCenterPage />} />
```

In `src/components/ui/Sidebar.tsx`, add a nav item in the Governance group (near `Sidebar.tsx:61`); reuse an existing imported icon (e.g. `Bell` if imported, else add `Bell` to the `lucide-react` import):

```tsx
        { to: "/notifications", icon: Bell, label: "Notifications" },
```

- [ ] **Step 5: Run tests + typecheck**

Run: `npm run test -- src/features/notifications/NotificationCenterPage.test.tsx && npm run typecheck`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add src/features/notifications src/app/App.tsx src/components/ui/Sidebar.tsx
git commit -m "feat(notifications): notification center page with channel CRUD

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

### Task 10: RBAC page — roles + IP-allowlist management

**Files:**
- Create: `src/features/rbac/RbacPage.tsx`, `RbacPage.test.tsx`
- Modify: `src/app/App.tsx`, `src/components/ui/Sidebar.tsx`

**Context:** Two sections. Roles: list assignments, create (`user_id` + `role` from a fixed select), delete. IP allowlist: list, add (`cidr` + `description`), delete. The role `<select>` options must be a fixed set; use `["admin", "approver", "operator", "viewer"]` (a safe subset — the backend validates against `VALID_ROLES` and returns 422 on a bad value, surfaced as a toast).

**Interfaces:**
- Consumes: `rbacApi.*` (Task 5), `Skeleton`/`EmptyState`, `toast`.
- Produces: `RbacPage()`; route `/rbac`.

- [ ] **Step 1: Write the failing test**

```tsx
// src/features/rbac/RbacPage.test.tsx
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter } from 'react-router-dom';
import { beforeEach, expect, test, vi } from 'vitest';
import { useAuthStore } from '@/stores/auth';
import { RbacPage } from './RbacPage';

function renderPage() {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={qc}>
      <MemoryRouter><RbacPage /></MemoryRouter>
    </QueryClientProvider>,
  );
}

beforeEach(() => {
  sessionStorage.clear(); localStorage.clear();
  useAuthStore.setState({ apiKey: 'k', tenantId: 't', plan: 'free', isAuthenticated: true });
});

test('lists roles and ip-allowlist entries', async () => {
  vi.spyOn(globalThis, 'fetch').mockImplementation(async (input) => {
    const url = String(input);
    if (url.includes('/tenants/me/roles'))
      return new Response(JSON.stringify([{ id: 'r1', user_id: 'alice', role: 'approver' }]),
        { status: 200, headers: { 'Content-Type': 'application/json' } });
    if (url.includes('/tenants/me/ip-allowlist'))
      return new Response(JSON.stringify([{ id: 'e1', cidr: '10.0.0.0/8', description: 'office' }]),
        { status: 200, headers: { 'Content-Type': 'application/json' } });
    return new Response('[]', { status: 200, headers: { 'Content-Type': 'application/json' } });
  });
  renderPage();
  expect(await screen.findByText('alice')).toBeInTheDocument();
  expect(await screen.findByText('10.0.0.0/8')).toBeInTheDocument();
});

test('add IP entry posts cidr', async () => {
  const f = vi.spyOn(globalThis, 'fetch').mockImplementation(async (input, init) => {
    const url = String(input);
    if (url.includes('/tenants/me/ip-allowlist') && init?.method === 'POST')
      return new Response(JSON.stringify({ id: 'e2', cidr: '192.168.0.0/16', description: '' }), { status: 201 });
    return new Response('[]', { status: 200, headers: { 'Content-Type': 'application/json' } });
  });
  renderPage();
  await screen.findByLabelText(/cidr/i);
  await userEvent.type(screen.getByLabelText(/cidr/i), '192.168.0.0/16');
  await userEvent.click(screen.getByRole('button', { name: /add cidr/i }));
  await waitFor(() =>
    expect(f.mock.calls.some(([u, i]) =>
      String(u).includes('/tenants/me/ip-allowlist') && (i as RequestInit)?.method === 'POST')).toBe(true),
  );
});
```

- [ ] **Step 2: Run test to verify it fails**

Run: `npm run test -- src/features/rbac/RbacPage.test.tsx`
Expected: FAIL — module not found.

- [ ] **Step 3: Implement the page**

```tsx
// src/features/rbac/RbacPage.tsx
import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Trash2 } from "lucide-react";
import { rbacApi } from "@/lib/api/client";
import { Skeleton } from "@/components/ui/Skeleton";
import { EmptyState } from "@/components/ui/EmptyState";
import { toast } from "@/stores/toast";

const ROLES = ["admin", "approver", "operator", "viewer"] as const;

export function RbacPage() {
  const qc = useQueryClient();
  const [userId, setUserId] = useState("");
  const [role, setRole] = useState<(typeof ROLES)[number]>("viewer");
  const [cidr, setCidr] = useState("");
  const [cidrDesc, setCidrDesc] = useState("");

  const roles = useQuery({ queryKey: ["rbac-roles"], queryFn: () => rbacApi.listRoles() });
  const ips = useQuery({ queryKey: ["rbac-ip"], queryFn: () => rbacApi.listIpAllowlist() });

  const createRole = useMutation({
    mutationFn: () => rbacApi.createRole(userId, role),
    onSuccess: () => {
      toast({ kind: "success", message: "Role assigned." });
      setUserId("");
      qc.invalidateQueries({ queryKey: ["rbac-roles"] });
    },
    onError: (e) => toast({ kind: "error", message: String(e) }),
  });
  const deleteRole = useMutation({
    mutationFn: (id: string) => rbacApi.deleteRole(id),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["rbac-roles"] }),
    onError: (e) => toast({ kind: "error", message: String(e) }),
  });
  const addIp = useMutation({
    mutationFn: () => rbacApi.addIpAllowlist(cidr, cidrDesc),
    onSuccess: () => {
      toast({ kind: "success", message: "CIDR added." });
      setCidr(""); setCidrDesc("");
      qc.invalidateQueries({ queryKey: ["rbac-ip"] });
    },
    onError: (e) => toast({ kind: "error", message: String(e) }),
  });
  const deleteIp = useMutation({
    mutationFn: (id: string) => rbacApi.deleteIpAllowlist(id),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["rbac-ip"] }),
    onError: (e) => toast({ kind: "error", message: String(e) }),
  });

  return (
    <div className="p-6 max-w-4xl mx-auto space-y-8">
      <div>
        <h1 className="text-2xl font-bold">Access Control</h1>
        <p className="text-muted-foreground text-sm mt-1">Manage team roles and network allowlists.</p>
      </div>

      {/* Roles */}
      <section className="space-y-3">
        <h2 className="font-semibold">Team roles</h2>
        <div className="flex flex-wrap gap-3 items-end bg-card border border-border rounded-xl p-4">
          <label className="flex flex-col gap-1 text-xs text-muted-foreground">
            User ID
            <input
              value={userId}
              onChange={(e) => setUserId(e.target.value)}
              className="px-2 py-1.5 border border-input rounded-md bg-background text-sm"
            />
          </label>
          <label className="flex flex-col gap-1 text-xs text-muted-foreground">
            Role
            <select
              value={role}
              onChange={(e) => setRole(e.target.value as (typeof ROLES)[number])}
              className="px-2 py-1.5 border border-input rounded-md bg-background text-sm"
            >
              {ROLES.map((r) => <option key={r} value={r}>{r}</option>)}
            </select>
          </label>
          <button
            onClick={() => createRole.mutate()}
            disabled={!userId || createRole.isPending}
            className="px-4 py-2 bg-primary text-primary-foreground rounded-md text-sm hover:opacity-90 disabled:opacity-50"
          >
            Assign role
          </button>
        </div>
        {roles.isLoading ? (
          <Skeleton className="h-10 w-full" />
        ) : (roles.data ?? []).length === 0 ? (
          <EmptyState title="No role assignments" />
        ) : (
          <div className="space-y-2">
            {(roles.data ?? []).map((r) => (
              <div key={r.id} className="flex items-center justify-between bg-card border border-border rounded-lg px-4 py-2.5 text-sm">
                <span><span className="font-medium">{r.user_id}</span> — {r.role}</span>
                <button onClick={() => deleteRole.mutate(r.id)} aria-label={`Remove role ${r.id}`} className="p-1.5 rounded-md hover:bg-accent text-muted-foreground">
                  <Trash2 className="h-4 w-4" aria-hidden="true" />
                </button>
              </div>
            ))}
          </div>
        )}
      </section>

      {/* IP allowlist */}
      <section className="space-y-3">
        <h2 className="font-semibold">IP allowlist</h2>
        <div className="flex flex-wrap gap-3 items-end bg-card border border-border rounded-xl p-4">
          <label className="flex flex-col gap-1 text-xs text-muted-foreground">
            CIDR
            <input
              value={cidr}
              onChange={(e) => setCidr(e.target.value)}
              placeholder="10.0.0.0/8"
              className="px-2 py-1.5 border border-input rounded-md bg-background text-sm font-mono"
            />
          </label>
          <label className="flex flex-col gap-1 text-xs text-muted-foreground flex-1 min-w-[12rem]">
            Description
            <input
              value={cidrDesc}
              onChange={(e) => setCidrDesc(e.target.value)}
              className="px-2 py-1.5 border border-input rounded-md bg-background text-sm"
            />
          </label>
          <button
            onClick={() => addIp.mutate()}
            disabled={!cidr || addIp.isPending}
            className="px-4 py-2 bg-primary text-primary-foreground rounded-md text-sm hover:opacity-90 disabled:opacity-50"
          >
            Add CIDR
          </button>
        </div>
        {ips.isLoading ? (
          <Skeleton className="h-10 w-full" />
        ) : (ips.data ?? []).length === 0 ? (
          <EmptyState title="No allowlist entries" description="All IPs are allowed until an entry is added." />
        ) : (
          <div className="space-y-2">
            {(ips.data ?? []).map((e) => (
              <div key={e.id} className="flex items-center justify-between bg-card border border-border rounded-lg px-4 py-2.5 text-sm">
                <span><span className="font-mono">{e.cidr}</span>{e.description ? ` — ${e.description}` : ""}</span>
                <button onClick={() => deleteIp.mutate(e.id)} aria-label={`Remove CIDR ${e.id}`} className="p-1.5 rounded-md hover:bg-accent text-muted-foreground">
                  <Trash2 className="h-4 w-4" aria-hidden="true" />
                </button>
              </div>
            ))}
          </div>
        )}
      </section>
    </div>
  );
}

export default RbacPage;
```

- [ ] **Step 4: Wire route + nav**

`src/app/App.tsx`:

```tsx
import { RbacPage } from "@/features/rbac/RbacPage";
// ...
        <Route path="rbac" element={<RbacPage />} />
```

`src/components/ui/Sidebar.tsx` — add to the Governance group (reuse `Shield` or add `KeyRound` to the `lucide-react` import):

```tsx
        { to: "/rbac", icon: KeyRound, label: "Access Control" },
```

- [ ] **Step 5: Run tests + typecheck**

Run: `npm run test -- src/features/rbac/RbacPage.test.tsx && npm run typecheck`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add src/features/rbac src/app/App.tsx src/components/ui/Sidebar.tsx
git commit -m "feat(rbac): roles and IP-allowlist management page

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

### Task 11: Compliance page — legal-hold status + GDPR async export polling + consent

**Files:**
- Create: `src/features/compliance/CompliancePage.tsx`, `CompliancePage.test.tsx`
- Modify: `src/app/App.tsx`, `src/components/ui/Sidebar.tsx`

**Context:** Three sections. **Legal holds:** list (`complianceApi.listLegalHolds`). **GDPR export:** "Start export" → `startGdprExport()` → store `job_id` and poll `getGdprExportStatus(jobId)` (TanStack Query `refetchInterval` until status is terminal); when `download_url` is present, show a download link. **Consent:** record/revoke by purpose (fixed select: `analytics`, `marketing`, `ai_processing`).

**Interfaces:**
- Consumes: `complianceApi.*` (Task 5), `Skeleton`/`EmptyState`/`StatusBadge`, `toast`.
- Produces: `CompliancePage()`; route `/compliance`.

- [ ] **Step 1: Write the failing test**

```tsx
// src/features/compliance/CompliancePage.test.tsx
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter } from 'react-router-dom';
import { beforeEach, expect, test, vi } from 'vitest';
import { useAuthStore } from '@/stores/auth';
import { CompliancePage } from './CompliancePage';

function renderPage() {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={qc}>
      <MemoryRouter><CompliancePage /></MemoryRouter>
    </QueryClientProvider>,
  );
}

beforeEach(() => {
  sessionStorage.clear(); localStorage.clear();
  useAuthStore.setState({ apiKey: 'k', tenantId: 't', plan: 'free', isAuthenticated: true });
});

test('lists legal holds', async () => {
  vi.spyOn(globalThis, 'fetch').mockImplementation(async (input) => {
    const url = String(input);
    if (url.includes('/governance/legal-holds'))
      return new Response(JSON.stringify([{ id: 'h1', reason: 'litigation', expires_at: null, created_by: 'admin' }]),
        { status: 200, headers: { 'Content-Type': 'application/json' } });
    return new Response('[]', { status: 200, headers: { 'Content-Type': 'application/json' } });
  });
  renderPage();
  expect(await screen.findByText(/litigation/i)).toBeInTheDocument();
});

test('starting a GDPR export begins polling the job', async () => {
  const f = vi.spyOn(globalThis, 'fetch').mockImplementation(async (input, init) => {
    const url = String(input);
    if (url.includes('/compliance/export/start') && init?.method === 'POST')
      return new Response(JSON.stringify({ job_id: 'j1', status: 'pending', poll_url: '/compliance/export/jobs/j1' }), { status: 200 });
    if (url.includes('/compliance/export/jobs/j1'))
      return new Response(JSON.stringify({ job_id: 'j1', status: 'complete', completed_at: '2026-06-28T00:00:00Z', download_url: 'https://x/export.zip', error: null }),
        { status: 200, headers: { 'Content-Type': 'application/json' } });
    return new Response('[]', { status: 200, headers: { 'Content-Type': 'application/json' } });
  });
  renderPage();
  await userEvent.click(await screen.findByRole('button', { name: /start gdpr export/i }));
  await waitFor(() =>
    expect(f.mock.calls.some(([u]) => String(u).includes('/compliance/export/jobs/j1'))).toBe(true),
  );
  expect(await screen.findByRole('link', { name: /download/i })).toBeInTheDocument();
});
```

- [ ] **Step 2: Run test to verify it fails**

Run: `npm run test -- src/features/compliance/CompliancePage.test.tsx`
Expected: FAIL — module not found.

- [ ] **Step 3: Implement the page**

```tsx
// src/features/compliance/CompliancePage.tsx
import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { complianceApi } from "@/lib/api/client";
import { Skeleton } from "@/components/ui/Skeleton";
import { EmptyState } from "@/components/ui/EmptyState";
import { StatusBadge } from "@/components/ui/StatusBadge";
import { toast } from "@/stores/toast";

const CONSENT_PURPOSES = ["analytics", "marketing", "ai_processing"] as const;
const TERMINAL_EXPORT = new Set(["complete", "completed", "failed"]);

export function CompliancePage() {
  const qc = useQueryClient();
  const [jobId, setJobId] = useState<string | null>(null);
  const [purpose, setPurpose] = useState<(typeof CONSENT_PURPOSES)[number]>("analytics");

  const holds = useQuery({ queryKey: ["legal-holds"], queryFn: () => complianceApi.listLegalHolds() });

  const exportStatus = useQuery({
    queryKey: ["gdpr-export", jobId],
    queryFn: () => complianceApi.getGdprExportStatus(jobId as string),
    enabled: jobId !== null,
    refetchInterval: (q) => {
      const s = (q.state.data as { status?: string } | undefined)?.status ?? "";
      return TERMINAL_EXPORT.has(s) ? false : 2000;
    },
  });

  const startExport = useMutation({
    mutationFn: () => complianceApi.startGdprExport(),
    onSuccess: (res) => {
      setJobId(res.job_id);
      toast({ kind: "info", message: "GDPR export started." });
    },
    onError: (e) => toast({ kind: "error", message: String(e) }),
  });

  const recordConsent = useMutation({
    mutationFn: () => complianceApi.recordConsent(purpose),
    onSuccess: () => toast({ kind: "success", message: `Consent recorded for ${purpose}.` }),
    onError: (e) => toast({ kind: "error", message: String(e) }),
  });
  const revokeConsent = useMutation({
    mutationFn: () => complianceApi.revokeConsent(purpose),
    onSuccess: () => toast({ kind: "success", message: `Consent revoked for ${purpose}.` }),
    onError: (e) => toast({ kind: "error", message: String(e) }),
  });

  const job = exportStatus.data;

  return (
    <div className="p-6 max-w-4xl mx-auto space-y-8">
      <div>
        <h1 className="text-2xl font-bold">Compliance</h1>
        <p className="text-muted-foreground text-sm mt-1">Legal holds, data export, and consent.</p>
      </div>

      {/* Legal holds */}
      <section className="space-y-3">
        <h2 className="font-semibold">Legal holds</h2>
        {holds.isLoading ? (
          <Skeleton className="h-10 w-full" />
        ) : (holds.data ?? []).length === 0 ? (
          <EmptyState title="No active legal holds" description="Data retention deletion runs normally." />
        ) : (
          <div className="space-y-2">
            {(holds.data ?? []).map((h) => (
              <div key={h.id} className="bg-card border border-border rounded-lg px-4 py-3 text-sm">
                <div className="font-medium">{h.reason}</div>
                <div className="text-xs text-muted-foreground mt-0.5">
                  by {h.created_by}{h.expires_at ? ` · expires ${h.expires_at}` : " · no expiry"}
                </div>
              </div>
            ))}
          </div>
        )}
      </section>

      {/* GDPR export */}
      <section className="space-y-3">
        <h2 className="font-semibold">GDPR data export</h2>
        <div className="bg-card border border-border rounded-xl p-4 space-y-3">
          <button
            onClick={() => startExport.mutate()}
            disabled={startExport.isPending}
            className="px-4 py-2 bg-primary text-primary-foreground rounded-md text-sm hover:opacity-90 disabled:opacity-50"
          >
            Start GDPR export
          </button>
          {jobId && job && (
            <div className="flex items-center gap-3 text-sm">
              <span className="text-muted-foreground">Job {jobId.slice(0, 8)}:</span>
              <StatusBadge status={job.status} />
              {job.download_url && (
                <a href={job.download_url} className="text-primary underline" download>
                  Download
                </a>
              )}
              {job.error && <span className="text-destructive">{job.error}</span>}
            </div>
          )}
        </div>
      </section>

      {/* Consent */}
      <section className="space-y-3">
        <h2 className="font-semibold">Consent management</h2>
        <div className="flex flex-wrap gap-3 items-end bg-card border border-border rounded-xl p-4">
          <label className="flex flex-col gap-1 text-xs text-muted-foreground">
            Purpose
            <select
              value={purpose}
              onChange={(e) => setPurpose(e.target.value as (typeof CONSENT_PURPOSES)[number])}
              className="px-2 py-1.5 border border-input rounded-md bg-background text-sm"
            >
              {CONSENT_PURPOSES.map((p) => <option key={p} value={p}>{p}</option>)}
            </select>
          </label>
          <button
            onClick={() => recordConsent.mutate()}
            disabled={recordConsent.isPending}
            className="px-4 py-2 bg-primary text-primary-foreground rounded-md text-sm hover:opacity-90 disabled:opacity-50"
          >
            Record consent
          </button>
          <button
            onClick={() => revokeConsent.mutate()}
            disabled={revokeConsent.isPending}
            className="px-4 py-2 bg-muted rounded-md text-sm hover:bg-accent disabled:opacity-50"
          >
            Revoke consent
          </button>
        </div>
      </section>
    </div>
  );
}

export default CompliancePage;
```

- [ ] **Step 4: Wire route + nav**

`src/app/App.tsx`:

```tsx
import { CompliancePage } from "@/features/compliance/CompliancePage";
// ...
        <Route path="compliance" element={<CompliancePage />} />
```

`src/components/ui/Sidebar.tsx` — add to the Governance group (reuse `Shield` or add `FileLock` to the `lucide-react` import):

```tsx
        { to: "/compliance", icon: FileLock, label: "Compliance" },
```

- [ ] **Step 5: Run tests + typecheck**

Run: `npm run test -- src/features/compliance/CompliancePage.test.tsx && npm run typecheck`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add src/features/compliance src/app/App.tsx src/components/ui/Sidebar.tsx
git commit -m "feat(compliance): legal-hold, GDPR export polling, consent page

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

### Task 12: Playwright e2e — Notification Center + Audit export

**Files:**
- Create: `e2e/notifications.spec.ts`, `e2e/audit-export.spec.ts`

**Context:** Use the existing `e2e/` `setupAuth` + route-mock helpers (mirror `e2e/governance.spec.ts`). Mock the relevant endpoints, then assert the happy path + one failure path.

**Interfaces:**
- Consumes: existing e2e helpers (`setupAuth`, route mocking) — inspect `e2e/governance.spec.ts` for the exact helper names/imports and reuse them verbatim.

- [ ] **Step 1: Write the e2e specs**

```ts
// e2e/notifications.spec.ts
import { test, expect } from '@playwright/test';
import { setupAuth } from './helpers'; // use the actual helper path/name from e2e/governance.spec.ts

test('create and delete a notification channel', async ({ page }) => {
  await setupAuth(page);
  let channels: Array<{ channel_id: string; type: string; enabled: boolean }> = [];
  await page.route('**/governance/notifications', async (route) => {
    if (route.request().method() === 'POST') {
      channels = [{ channel_id: 'c1', type: 'webhook', enabled: true }];
      await route.fulfill({ status: 201, json: { channel_id: 'c1', type: 'webhook', status: 'created' } });
    } else {
      await route.fulfill({ status: 200, json: channels });
    }
  });
  await page.goto('/notifications');
  await page.getByRole('button', { name: /add channel/i }).click();
  await expect(page.getByText('webhook')).toBeVisible();
});

test('shows error toast on server failure', async ({ page }) => {
  await setupAuth(page);
  await page.route('**/governance/notifications', async (route) => {
    if (route.request().method() === 'POST')
      await route.fulfill({ status: 500, json: { error: { message: 'boom' } } });
    else await route.fulfill({ status: 200, json: [] });
  });
  await page.goto('/notifications');
  await page.getByRole('button', { name: /add channel/i }).click();
  await expect(page.getByRole('status')).toContainText(/boom|error/i);
});
```

```ts
// e2e/audit-export.spec.ts
import { test, expect } from '@playwright/test';
import { setupAuth } from './helpers';

test('audit explorer renders rows and exposes export buttons', async ({ page }) => {
  await setupAuth(page);
  await page.route('**/governance/audit*', async (route) => {
    await route.fulfill({
      status: 200,
      json: [{ event_id: 'e1', goal_id: 'g1', tool_name: 'jira.delete', action_level: 'deny', outcome: 'denied' }],
    });
  });
  await page.goto('/audit');
  await expect(page.getByText('jira.delete')).toBeVisible();
  await expect(page.getByRole('button', { name: /export csv/i })).toBeVisible();
  await expect(page.getByRole('button', { name: /export json/i })).toBeVisible();
});
```

- [ ] **Step 2: Run the e2e specs**

Run: `npm run test:e2e -- e2e/notifications.spec.ts e2e/audit-export.spec.ts`
Expected: PASS. (If `setupAuth`/route helpers live elsewhere, fix the import to match `e2e/governance.spec.ts` exactly before re-running.)

- [ ] **Step 3: Commit**

```bash
git add e2e/notifications.spec.ts e2e/audit-export.spec.ts
git commit -m "test(e2e): notification center and audit export flows

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

### Task 13: Phase-3 regression gate

**Files:** none (verification only)

- [ ] **Step 1: Backend — additive endpoints green**

Run: `cd agent-verse-backend && uv run pytest tests/api/test_governance_streams.py tests/api/test_governance_api.py && uv run ruff check app/api/governance.py && uv run mypy app/api/governance.py`
Expected: all pass; no existing governance test regressed.

- [ ] **Step 2: Frontend typecheck**

Run: `cd agent-verse-frontend && npm run typecheck`
Expected: no errors.

- [ ] **Step 3: Frontend lint**

Run: `npm run lint`
Expected: no errors (pre-existing warnings acceptable).

- [ ] **Step 4: Full unit suite**

Run: `npm run test`
Expected: all pass; coverage not decreased.

- [ ] **Step 5: E2E smoke (existing + new must pass)**

Run: `npm run test:e2e -- e2e/governance.spec.ts e2e/navigation.spec.ts e2e/notifications.spec.ts e2e/audit-export.spec.ts`
Expected: PASS. New routes (`/notifications`, `/rbac`, `/compliance`) are additive; existing nav unaffected.

- [ ] **Step 6: Tag the phase**

```bash
git tag -a frontend-phase3 -m "Frontend Phase 3: governance & real-time"
```

---

## Self-Review

**Spec coverage (against WS-2 / P1-1, P1-2, P1-3, P1-9):**
- Generic SSE hook (phase-owned) → Task 1 (`useEventStream`). ✅
- Additive backend SSE endpoints → Tasks 2 (`/governance/approvals/stream`), 3 (`/governance/policies/stream`), wrapping the existing `platform_events` and `policy_changes` Redis pub/sub, with pytest. ✅
- Notification center + channel CRUD (P1-2) → Tasks 4 (backend DELETE), 9 (page). Delivery logs surfaced as "not yet available" because no backend endpoint records them (verified — `NotificationService` has no delivery log). ✅
- Real-time approvals + TopBar counter (P1-3) → Tasks 7, 8. ✅
- Typed Audit Explorer + filters + CSV/JSON export (replaces `any[]`) → Tasks 5 (`auditApi`), 6 (page). ✅
- Legal-hold status + GDPR async-export polling + consent (P1-9) → Tasks 4 (backend GET legal-holds), 5 (`complianceApi`), 11 (page). ✅
- RBAC roles + IP-allowlist (P1-1) → Tasks 5 (`rbacApi`), 10 (page). ✅
- e2e + regression gate → Tasks 12, 13. ✅

**Verified backend corrections (vs. naive assumptions):** No `DELETE /governance/notifications/{id}` existed (service `remove_channel` did) — added in Task 4; no delivery-logs endpoint exists at all — UI states this, does not fabricate. No `GET /governance/legal-holds` existed (only POST) — added in Task 4. GDPR export poll path is `/compliance/export/jobs/{job_id}` (router prefix `/compliance`, `enterprise.py:15`, mounted `main.py:848`), not `/enterprise/...`. Approve/reject routes are gated by `require_role("approver")` — the SSE-stream test `_CTX` includes `roles=("admin","approver")`.

**Interface ownership (this phase OWNS):** `useEventStream`; `GET /governance/approvals/stream`; `GET /governance/policies/stream`; `notificationsApi`; `auditApi`; `rbacApi` (plus `complianceApi` and the two `governanceApi` stream-path helpers). Phase 8 consuming `GET /goals/{id}/decision-traces` (Phase-5-owned) is explicitly out of scope.

**Placeholder scan:** none — every code step contains complete, runnable code; every run step has an exact command + expected result. The only deliberate "fill from existing file" note is the e2e `setupAuth` import path (Task 12), which must match `e2e/governance.spec.ts` — flagged, not stubbed.

**Type consistency:** `StreamEvent`/`UseEventStreamOptions`/`useEventStream` consistent across Tasks 1, 7, 8. `AuditEvent`/`AuditQuery` consistent across Tasks 5, 6. `NotificationChannel`/`CreateNotificationChannelRequest` consistent across Tasks 5, 9. `RoleAssignment`/`IpAllowlistEntry` consistent across Tasks 5, 10. `LegalHold`/`GdprExportJob`/`complianceApi` consistent across Tasks 5, 11. `governanceApi.approvalsStreamPath()` consistent across Tasks 5, 7, 8.

---

## Execution Handoff

Phase 3 depends on Phase 1 foundations (`toast`/`useToastStore`, the 401 contract in `client.ts`, `Skeleton`/`EmptyState`/`StatusBadge`). It delivers the phase-owned `useEventStream` hook and two additive SSE backend endpoints that **later phases consume** (the Civilization map and Workflow-Builder live execution can both build on `useEventStream`). All new client modules (`notificationsApi`, `auditApi`, `rbacApi`, `complianceApi`) and the stream-path helpers are additive to `client.ts` and safe for other phases to import.
