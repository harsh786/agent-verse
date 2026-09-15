# Reliability Hardening — kill the "silently broken" bug classes

> Two bugs bit us in the UI (chat read an API key nothing writes → 401 → dead buttons;
> a backend port mismatch). Full audit shows both are **instances of larger classes**
> with many live copies. This plan fixes the classes, front to back, so controls never
> fail silently and features never *look* done while doing nothing.

Legend: `[ ]` todo · `[~]` doing · `[x]` done (tested). Order = highest impact / lowest risk first.

## Bug classes (from the audit)
- **A. Auth-key sourcing drift** — ~28 sites hand-roll `X-API-Key` from storage or the store
  instead of the canonical `getAuthHeader()`/shared client; most also omit `Authorization:
  Bearer` → **break under SSO**. Storage-only readers 401 whenever `av_api_key` isn't written.
- **B. Base-URL / env drift** — committed `.env*` define a **dead** `VITE_API_URL`; `VITE_API_BASE_URL`
  is unset → client falls back to hardcoded `http://localhost:8000`. A clean checkout and every
  **prod build point at the wrong host**. Plus duplicated `localhost:8000` defaults + `|| ''`
  relative variants + unproxied relative paths that 404 even in dev.
- **C. Silent failure handlers** — async onClick/mutations with no try/catch and no error toast
  → the control looks dead (the exact "New Chat does nothing" UX).
- **D. Facade endpoints** — return success/"connected"/canned data while doing no real work.
- **E. In-memory stores masquerading as persistent** — chat REST uses module-level singletons
  (MemoryAPI, ServicesAPI, chat TemplateStore, StateMachine) that evaporate on restart though
  docstrings claim DB backing.

---

## Phase 1 — Quick wins (do first; high impact, low risk)

Frontend
- [ ] **B1. Fix committed env files.** `.env`, `.env.example`, `.env.production`: replace the dead
  `VITE_API_URL` with `VITE_API_BASE_URL` (dev `http://localhost:8001`, prod real host) + matching
  `VITE_WS_URL`. Prevents "clean checkout hits :8000 / prod UI calls localhost."
- [ ] **C1. Chat session mutations fail loudly.** Add `onError` toasts to `useChatSession`
  create/delete/pin/rename (or wrap the ChatPage handlers) so a failed "New Chat" surfaces an
  error instead of doing nothing. Regression-proofs the bug we just fixed. *(test)*
- [ ] **A1. Convert the storage-only auth readers** to `getAuthHeader()` (SSO-safe): `AgentMemoryPage`,
  `ConnectedServicesPanel`, `SimulationPage`. Fix their unproxied/relative paths at the same time.
- [ ] **B2. Fix 404-in-dev relative paths**: `CostBreakdown` (`/api/goals/...`), `GoalRunInspector`
  (`/api/observability/...`) → use `API_BASE` absolute URL (they only work through the un-proxied
  path today).

Backend
- [ ] **D1. QA turn fails loud when no LLM is wired.** In `chat/router.py`, when `answer_generator
  is None`, return a clear error / health signal instead of echoing `"Answering: {msg}"` with a
  fabricated `cost_usd`. *(test)*
- [ ] **C2/D2. Schedule turn stops lying.** In `chat/router.py:327-341`, if `create_schedule`
  throws, surface the failure — do NOT emit `SCHEDULE_CREATED` with empty ids. *(test)*

## Phase 2 — Auth/base-URL standardization (the biggest correctness gap)
- [ ] **A2. Standardize auth on `getAuthHeader()` / the shared `apiFetch`** across the ~25
  hand-rolled `X-API-Key` sites (they silently 401 under Keycloak SSO because they never send a
  Bearer). Do the high-traffic pages first (TopBar search, AppLayout emergency-stop, App session
  validation, agents/observability/security panels, goals feedback/explain, knowledge, eval,
  skills, playground, lab, builder, connectors).
- [ ] **B3. Single source of truth for base URL** — import `API_BASE` from `client.ts` everywhere;
  delete duplicated `http://localhost:8000` defaults and the `|| ''` variants.
- [ ] **A3. Chat client sends Bearer too** (`chat.ts` `headers()`), for SSO parity.

## Phase 3 — Route endpoints to the real (DB-backed) stores; kill facades
- [ ] **E1. `/chat/memories` → `LongTermMemoryStore`** (Postgres+pgvector) instead of the in-memory
  `MemoryAPI` singleton; wire via `app.state` (mirror how `api/templates.py` is DB-wired). Highest —
  user-trusted data + a GDPR-delete that currently only clears a dict.
- [ ] **E2. Chat `TemplateStore` → the DB-backed `api/templates` store**; **StateMachine registry →
  DB**.
- [ ] **D3/E3. `/chat/services` + OAuth connector completion**: stop returning `"connected"` for
  integrations never registered/token-exchanged — either implement real MCP registration + per-
  provider token exchange, or return an honest `pending`/`not_configured` status. *(large)*
- [ ] **D4. Members / invite / BYOK vault-key** persist for real (or return honest `not_implemented`).

## Phase 4 — Cleanup
- [ ] Delete or wire the 3 orphaned chat components (`ChatDiff`, `ChatRichOutput`, `ChatStepCard`).
- [ ] `/ingestion/documents` (always `[]`) and analytics `traces` (fabricated 500ms spans): back
  with real data or label honestly.

## Testing
Each `[x]` ships with a test where logic changed (vitest for FE handlers/hooks, pytest for BE
routes). Frontend: `npm run test`, `tsc`, `eslint`. Backend: `uv run pytest`, ruff, mypy. Verify the
live dashboard after auth/env changes.
