# Situation Room UX Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the Autonomous Org Brain *visible and legible* — a live "Situation Room" where
you watch agents think, talk, act, and spend in real time, with every message, decision, and
action traceable and timed.

**Architecture:** Full-stack, additive over the merged org-brain feature. Backend enriches the
collaboration + decision + audit data (typed inter-agent messages with from→to/latency/tokens/$,
guardrail-attributed decisions, per-agent action trail, mission phase timing) and exposes it via
new/extended `/v1/org/*` endpoints and the existing SSE stream. Frontend adds/upgrades org
command-center surfaces that consume it. Nothing weakens the org-brain safety model.

**Tech Stack:** Backend — Python 3.12, FastAPI, SQLAlchemy 2 async, Alembic, Celery, Redis,
Postgres (RLS). Frontend — React 19, Vite, TanStack Query, Tailwind, framer-motion, vitest +
React Testing Library.

**Spec:** `docs/superpowers/specs/2026-09-14-situation-room-ux-design.md`

## Global Constraints

- **Safety unchanged:** never weaken org-brain guardrails, autonomy defaults (L1 no-op, flag off,
  `AV_ORG_AUTONOMY_DISABLED`), fail-closed behavior, or HITL. UX is read-mostly; the only writes
  are existing autonomy PATCH / approve-reject.
- **Tenant/org isolation:** every new query/endpoint is RLS-scoped + explicit `tenant_id` filter;
  cross-org access returns 404 (mirror the approve/reject pattern in `router.py`).
- **Additive & backward-compatible:** enriching the `org.collaboration.message` payload must keep
  existing consumers working; new fields are optional. Do not break existing tests.
- **Backend style:** ruff (line-length 100, py312, `E,F,I,N,UP,B,A,C4,SIM,RUF`), mypy strict with
  the pydantic plugin. Tests mirror source under `tests/<pkg>/`. `pytest` treats warnings as errors.
  Always `uv run` backend commands. Regenerate OpenAPI (`uv run python scripts/export_openapi.py`)
  when routes change.
- **Frontend style:** match existing org feature conventions — components in
  `src/features/org/components/`, HTTP via `apiFetch`/`orgApi`/`orgAutonomyApi` in
  `src/features/org/api.ts` (`const BASE = '/v1/org'`), types in `types.ts`, TanStack Query for
  server state. Respect `prefers-reduced-motion` in every animation (mirror existing components).
  Theme-aware dark JARVIS styling with cyan accents; `cn` from `@/lib/utils`. Tests mirror
  `__tests__/org.test.tsx` (QueryClientProvider + MemoryRouter wrap; framer-motion mocked).
- **Migrations:** never edit a deployed migration; add a new one. Current head is `0131_merge_heads`.
- **Integration tests:** real Postgres via testcontainers with
  `DOCKER_HOST=unix:///Users/harsh/.colima/default/docker.sock` and `TESTCONTAINERS_RYUK_DISABLED=true`.
- **Reduced-motion + a11y:** all interactive controls carry accessible names; animations are
  decorative and gated behind `useReducedMotion`.

---

## File Structure

Backend (`agent-verse-backend/`):
- Modify `app/org/brain_collaboration.py` — enrich published payload + capture real cost/latency/tokens.
- Modify `app/scaling/tasks.py::_collaboration_tick_for_org` — pass agent identities + persist path.
- Modify `app/org/events.py` / `app/org/service.py` — persist collaboration events to `org_events`.
- Modify `app/org/brain_guardrails.py` — return per-check trace; `app/org/brain_store.py` + `app/org/brain.py` — persist it.
- New migration `app/db/migrations/versions/0132_org_brain_guardrail_trace.py` — add `guardrail_trace` JSONB.
- Modify `app/org/service.py::list_events` + `app/org/router.py` — `entity_id` filter + agent-audit + mission-timeline endpoints.
- Tests under `tests/org/`.

Frontend (`agent-verse-frontend/src/features/org/`):
- Modify `api.ts` + `types.ts` — new methods/types.
- Rewrite `components/TeamChannel.tsx` (+ new `__tests__/TeamChannel.test.tsx`).
- Rewrite `components/BrainFeed.tsx` (+ extend `__tests__/BrainFeed.test.tsx`).
- Modify `hooks/useOrgNeuralState.ts` + `components/AgentConstellation.tsx` — beams from real messages.
- Wire `AgentProfile.tsx` drawer + `OrgPage.tsx` selection.
- New `components/MissionGantt.tsx`, `components/BudgetGauges.tsx`, `components/NarrationTicker.tsx`.
- Modify `OrgPage.tsx` — mount cohesively.

---

### Task 1: Backend — enrich collaboration message payload (real cost/latency/tokens/typed)

**Files:**
- Modify: `agent-verse-backend/app/org/brain_collaboration.py`
- Modify: `agent-verse-backend/app/scaling/tasks.py` (`_collaboration_tick_for_org`)
- Test: `agent-verse-backend/tests/org/test_brain_collaboration.py`

**Interfaces:**
- Produces: enriched `org.collaboration.message` payload (additive — keeps `lead`, `message`):
  `{lead, message, from_agent: str, to: str, kind: str, latency_ms: int, tokens: int, cost_usd: float, mission_id: str|None}`.
  `kind ∈ {"update","proposal","question","handoff","result","risk","block"}` — default `"update"`; may be
  a lightweight keyword classification of `message` (e.g. "?"→question, "propose/suggest"→proposal,
  "risk/blocked/cannot"→risk). `from_agent` = the lead's agent id/name. `to` = `"team"` (broadcast model).
- The gateway `complete_short` must return real usage: extend it to also return `latency_ms` (wall-clock
  around `provider.complete`) and `tokens`/`cost_usd` from the response (`resp.usage`/`resp.cost` when present,
  else fall back to `_EST_COST_USD_PER_MESSAGE` and a token estimate). Record the REAL cost to
  `counters.record_collab_spend(cost_usd)` instead of the fixed estimate when available.

- [ ] Step 1: Write failing tests — (a) `complete_short` returns `(text, latency_ms, tokens, cost_usd)`;
  (b) `CollaborationTick.run` publishes a payload containing `from_agent`, `to=="team"`, a valid `kind`,
  `latency_ms>=0`, `tokens>=0`, `cost_usd>=0`; (c) backward-compat: `lead` and `message` still present;
  (d) real cost (when gateway reports it) is what feeds `record_collab_spend`.
- [ ] Step 2: Run → FAIL.
- [ ] Step 3: Implement the payload enrichment + usage capture + kind classifier (small pure helper
  `classify_message_kind(text) -> str`, unit-tested).
- [ ] Step 4: `uv run pytest tests/org/test_brain_collaboration.py -v` PASS; `uv run ruff check` + `uv run mypy app/org/brain_collaboration.py` clean; commit.

---

### Task 2: Backend — persist collaboration messages to `org_events` (history + agent-queryable)

**Files:**
- Modify: `agent-verse-backend/app/org/brain_collaboration.py` (or the tick) to persist each message.
- Modify: `agent-verse-backend/app/org/service.py` if a persist helper is the cleaner path.
- Test: `agent-verse-backend/tests/org/test_collaboration_persistence.py` (integration, testcontainers).

**Interfaces:**
- Consumes: Task 1's enriched payload.
- Produces: one `org_events` row per collaboration message — `event_type="org.collaboration.message"`,
  `entity_type="agent"`, `entity_id=from_agent`, `actor_id=from_agent`, `severity="info"`,
  `title=f"{from_agent} · {kind}"`, `description=message` (truncated), `payload=<enriched payload>`,
  `source="collaboration"`. Written inside the RLS-scoped session the tick already holds, so it commits with
  the tick transaction. SSE publish still happens (unchanged) — persistence is additive.

- [ ] Step 1: Write a failing integration test (real PG): after a collaboration tick, `list_events(org_id,
  tenant_id, event_type="org.collaboration.message")` returns rows with the right `entity_id`/payload.
- [ ] Step 2: Run → FAIL.
- [ ] Step 3: Implement persistence (reuse `OrgService._emit_event` semantics or a direct insert that matches
  the `org_events` columns; ensure tenant/org scoping).
- [ ] Step 4: integration test PASS (with `DOCKER_HOST`/`TESTCONTAINERS_RYUK_DISABLED`); ruff+mypy clean; commit.

---

### Task 3: Backend — persist the 8-check guardrail trace (Brain Feed v2 data)

**Files:**
- New: `agent-verse-backend/app/db/migrations/versions/0132_org_brain_guardrail_trace.py` (add nullable
  `guardrail_trace JSONB` to `org_brain_decisions`).
- Modify: `app/org/models.py` (column), `app/org/brain_guardrails.py` (return per-check results),
  `app/org/brain_store.py` (persist + return), `app/org/brain.py` (thread the trace through).
- Test: `tests/org/test_brain_guardrails.py`, `tests/org/test_brain_store.py`.

**Interfaces:**
- `evaluate_guardrails(...)` additionally returns an ordered `checks: list[GuardrailCheck]` where
  `GuardrailCheck = {name: str, passed: bool, detail: str, value: str|None, limit: str|None}` for each of the
  8 checks (killswitch, level, cooldown, daily_cap, concurrency, dedup, cost, risk). Keep the existing
  `Verdict(action, reason)` return; add checks as a second value or an attribute — pick the least-breaking shape
  and update all callers. `value`/`limit` carry the numbers where applicable (e.g. daily cap "2/2",
  budget "$4.80/$5.00").
- `BrainDecisionStore.record(..., guardrail_trace=checks)` persists it; `.list(...)` returns it as
  `guardrail_trace` in each row dict.

- [ ] Step 1: Write failing tests — guardrails return a checks list with the blocking check flagged + its
  number; store round-trips `guardrail_trace`; migration upgrades/downgrades cleanly.
- [ ] Step 2: Run → FAIL.
- [ ] Step 3: Implement migration + model column + checks assembly + store persistence.
- [ ] Step 4: `uv run alembic upgrade head` then `downgrade -1` then `upgrade head` clean; unit + integration
  tests PASS; ruff+mypy clean; regenerate OpenAPI if the decisions response shape changed; commit.

---

### Task 4: Backend — agent-scoped audit endpoint + `entity_id` filter

**Files:**
- Modify: `app/org/service.py` (`list_events` gains `entity_id`/`entity_type` filter; new aggregation method
  `get_agent_audit`), `app/org/router.py` (new endpoint + filter passthrough).
- Test: `tests/org/test_agent_audit.py`.

**Interfaces:**
- `GET /v1/org/{org_id}/agents/{agent_id}/audit?limit=50` → `list[AgentAuditEntry]` newest-first, where
  `AgentAuditEntry = {id, kind: "message"|"decision"|"task"|"event", at: iso, title, detail, cost_usd: float|None,
  duration_ms: int|None, mission_id: str|None, ref: dict}`. Assembled by unioning: `org_events` where
  `entity_id==agent_id`; `org_decisions` where `actor_agent_id==agent_id`; `org_tasks` where
  `owner_agent_id==agent_id` or `agent_id ∈ assigned_agent_ids`. RLS-scoped + explicit tenant filter; sorted by
  time desc; capped by `limit`. Cross-org isolation preserved (agent ids are org-local strings; scope every
  query by org_id).
- `list_events(..., entity_id=None)` adds an optional exact-match filter on the existing `entity_id` column.

- [ ] Step 1: Write failing tests (integration) — seed events/decisions/tasks for agent A and agent B; assert
  `get_agent_audit(A)` returns only A's items, unified + time-ordered, and B's are excluded.
- [ ] Step 2: Run → FAIL.
- [ ] Step 3: Implement filter + aggregation + endpoint.
- [ ] Step 4: tests PASS; ruff+mypy clean; regenerate OpenAPI; commit.

---

### Task 5: Backend — mission timeline endpoint (Gantt data)

**Files:**
- Modify: `app/org/service.py` (`get_mission_timeline`), `app/org/router.py` (endpoint).
- Test: `tests/org/test_mission_timeline.py`.

**Interfaces:**
- `GET /v1/org/{org_id}/missions/{mission_id}/timeline` → `{mission_id, phases: list[Phase], total_ms: int|None}`
  where `Phase = {name: str, at: iso, until: iso|None, duration_ms: int|None, agent: str|None}`. Phases are
  derived from `org_events` filtered by `entity_id==mission_id` (mission.created/started, team.formed,
  agent.completed_task, mission.completed/failed) plus `OrgMission.started_at/completed_at`, collapsed into an
  ordered phase ribbon (created → planned → executing → verifying → done), each with its duration. Cross-org 404.

- [ ] Step 1: Write failing test — seed a mission + its lifecycle events; assert ordered phases with durations.
- [ ] Step 2: Run → FAIL. Step 3: Implement. Step 4: tests PASS; ruff+mypy; OpenAPI; commit.

---

### Task 6: Frontend — API client + types for the new data

**Files:**
- Modify: `agent-verse-frontend/src/features/org/api.ts`, `agent-verse-frontend/src/features/org/types.ts`
- Test: `src/features/org/__tests__/situationRoomApi.test.ts`

**Interfaces (add to `orgApi`/a new `situationApi` in `api.ts`, BASE `/v1/org`):**
- `collaborationHistory(orgId, limit?)` → `GET /v1/org/{id}/events?event_type=org.collaboration.message&limit=`
  → `CollaborationMessage[]` (map from the `OrgEvent` REST rows' payloads).
- `agentAudit(orgId, agentId, limit?)` → `GET /v1/org/{id}/agents/{agentId}/audit?limit=` → `AgentAuditEntry[]`.
- `missionTimeline(orgId, missionId)` → `GET /v1/org/{id}/missions/{missionId}/timeline` → `MissionTimeline`.
- Types in `types.ts`: `CollaborationMessage {id, from_agent, to, kind, message, latency_ms?, tokens?, cost_usd?, mission_id?, at}`,
  `AgentAuditEntry`, `MissionTimeline`/`MissionPhase`, `GuardrailCheck`, and extend `BrainDecision` with
  `guardrail_trace?: GuardrailCheck[]`.

- [ ] Step 1: Write failing vitest (stub `fetch`, assert URL+method per method). Step 2: FAIL. Step 3: Implement.
  Step 4: `npm run typecheck` + `npm run test -- src/features/org/__tests__/situationRoomApi.test.ts` PASS; commit.

---

### Task 7: Frontend — Team Channel v2 (Situation Room chat)

**Files:**
- Rewrite: `src/features/org/components/TeamChannel.tsx`
- Test (new): `src/features/org/__tests__/TeamChannel.test.tsx`

**Interfaces:** `TeamChannel({ orgId, className, maxItems? })`. Loads recent history via
`situationApi.collaborationHistory(orgId)` (TanStack Query) AND appends live via
`useOrgRealtimeManager(orgId,{onEvent})` filtering `ORG_EVENTS.COLLABORATION_MESSAGE` (dedupe by id). Each row:
avatar/initials for `from_agent`, a **kind chip** with icon (proposal 💡 / question ❓ / handoff 🤝 / result ✅ /
risk ⚠️ / block ⛔ / update •), the message text, and a compact meta line (`from → to · latency ms · tokens · $`).
Row is expandable → **payload inspector**: full from/to, kind, mission link (if `mission_id`), and the raw
payload JSON. Empty/loading states. `useCallback` onEvent (stable). Respect reduced motion.

- [ ] Step 1: Write failing test — mock `situationApi` (2 messages incl. a `risk` + a `proposal`) and
  `../OrgRealtimeManager` (`useOrgRealtimeManager` capturing onEvent + `ORG_EVENTS`); assert both render with
  their kind chips and meta (latency/tokens/$), expanding a row shows the payload, and a live event pushed
  through the captured `onEvent` appends a new row. Mirror `BrainFeed.test.tsx` mocking + `wrap()`.
- [ ] Step 2: FAIL. Step 3: Implement. Step 4: `npm run typecheck` + test PASS; commit.

---

### Task 8: Frontend — Brain Feed v2 (guardrail-attributed, expandable trace)

**Files:**
- Rewrite/extend: `src/features/org/components/BrainFeed.tsx`
- Test: extend `src/features/org/__tests__/BrainFeed.test.tsx`

**Interfaces:** unchanged props. For held-back rows, show **which guardrail** stopped it with its number,
derived from `guardrail_trace` (the failing check's `name` + `value`/`limit`, e.g. "Daily budget · $4.80/$5.00")
falling back to `reason` text when `guardrail_trace` absent. Each row expands to the full ordered 8-check trace
(pass ✓ / fail ✗ with detail + numbers) — the SENSE→DECIDE→GUARD→ACT view. Mission deep-link when `mission_id`.
Optionally refresh on `ORG_EVENTS.DECISION_RECORDED`/collaboration ticks (invalidate the query). Keep held-back
styling.

- [ ] Step 1: Write failing tests — a blocked decision with a `guardrail_trace` renders the specific guardrail
  name + number and an expandable full trace; a decision without trace falls back to `reason`. Step 2: FAIL.
  Step 3: Implement. Step 4: typecheck + test PASS; commit.

---

### Task 9: Frontend — Live Agent Network beams from real messages

**Files:**
- Modify: `src/features/org/hooks/useOrgNeuralState.ts` (feed `communicatingPairs` + a `recentMessages` buffer
  from `ORG_EVENTS.COLLABORATION_MESSAGE`), `src/features/org/components/AgentConstellation.tsx` (color beams by
  `kind`, hover tooltip with payload summary, click → callback), and expose an `onBeamSelect(messageId)` prop.
- Test: `src/features/org/__tests__/useOrgNeuralState.test.ts` (+ light component test).

**Interfaces:** `useOrgNeuralState` reducer handles `COLLABORATION_MESSAGE`: add `{from_agent → to}` to
`communicatingPairs` (transient, decays after N seconds) and push to a capped `recentMessages` list carrying
`{id, from, to, kind, at}`. `AgentConstellation` colors the comm-beam by kind, shows a hover tooltip
(from→to · kind), and calls `onBeamSelect(messageId)` / `onAgentSelect(agentId)` on click. Keep SMIL animations
gated behind reduced-motion. (Directed from→to uses "team" as a virtual target node when `to==="team"`.)

- [ ] Step 1: Write failing reducer test — applying a `COLLABORATION_MESSAGE` event adds a communicating pair +
  a recent message; it decays/caps as specified. Step 2: FAIL. Step 3: Implement reducer + wire component props.
  Step 4: typecheck + test PASS; commit.

---

### Task 10: Frontend — Agent profile drawer wired to the audit trail

**Files:**
- Modify: `src/features/org/OrgPage.tsx` (pass `onAgentSelect`/`selectedAgentId` to `AgentConstellation`; render
  the drawer), reuse `src/features/org/AgentProfile.tsx` (or a new `components/AgentAuditDrawer.tsx` if
  AgentProfile's shape doesn't fit), consuming `situationApi.agentAudit`.
- Test: `src/features/org/__tests__/AgentAuditDrawer.test.tsx`

**Interfaces:** clicking an agent node (via the now-wired `onAgentSelect`) opens a drawer showing the agent's
unified audit trail (messages/decisions/tasks/events, timestamped, cost + duration), with an **"explain"**
affordance on decision entries that links to the brain decision / guardrail verdict. Loading/empty/error states.

- [ ] Step 1: Write failing test — mock `situationApi.agentAudit` (mixed entries), render drawer for an agent,
  assert entries render with kind + time + cost and the explain link on a decision entry. Step 2: FAIL.
  Step 3: Implement + wire OrgPage selection. Step 4: typecheck + test PASS; commit.

---

### Task 11: Frontend — timing layer (mission Gantt + budget gauges)

**Files:**
- New: `src/features/org/components/MissionGantt.tsx`, `src/features/org/components/BudgetGauges.tsx`
- Modify: `src/features/org/components/MissionDetail.tsx` (embed the Gantt) and `OrgPage.tsx` (gauges in panel).
- Test: `src/features/org/__tests__/MissionGantt.test.tsx`, `__tests__/BudgetGauges.test.tsx`

**Interfaces:** `MissionGantt({ orgId, missionId })` consumes `situationApi.missionTimeline` → a horizontal
phase ribbon with per-phase durations + responsible agent on hover, and a live clock for the active phase.
`BudgetGauges({ orgId })` reads `orgAutonomyApi.get` (caps) + brain decisions' `est_cost_usd` sum for the day
(client-side aggregate) to render radial gauges for daily / per-mission-ceiling / collaboration budgets that
fill and turn amber near the cap. Reduced-motion friendly.

- [ ] Step 1: Write failing tests — Gantt renders phases with durations from a mocked timeline; gauges render
  fill % from mocked caps+spend and flag amber near cap. Step 2: FAIL. Step 3: Implement. Step 4: typecheck +
  tests PASS; commit.

---

### Task 12: Frontend — craft pass (narration ticker + hero Pause + motion)

**Files:**
- New: `src/features/org/components/NarrationTicker.tsx`
- Modify: `OrgPage.tsx` (mount ticker; elevate the Pause/emergency control prominence).
- Test: `src/features/org/__tests__/NarrationTicker.test.tsx`

**Interfaces:** `NarrationTicker({ orgId })` subscribes via `useOrgRealtimeManager` and renders a one-line,
human-readable running commentary from events (brain decisions, collaboration messages, mission state changes),
newest first, capped, reduced-motion aware. Elevate the existing Pause (from AutonomyControl) / emergency-stop
into a clearly visible, always-present control with an explicit state label. No new backend.

- [ ] Step 1: Write failing test — pushing a few mocked events through the captured `onEvent` renders readable
  narration lines in order. Step 2: FAIL. Step 3: Implement. Step 4: typecheck + test PASS; commit.

---

### Task 13: Frontend — OrgPage cohesion + full wiring

**Files:**
- Modify: `src/features/org/OrgPage.tsx`
- Test: extend `src/features/org/__tests__/org.test.tsx` (smoke that the panel renders the new surfaces).

**Steps:** mount Team Channel v2, Brain Feed v2, Budget Gauges, Narration Ticker, and the agent drawer trigger
cohesively in the command panel; ensure the network beams + drawer selection are wired; keep the panel resizable
and existing sections intact. `npm run typecheck && npm run test -- src/features/org && npm run build` all green.
Commit.

---

### Task 14: End-to-end verification (backend + frontend + browser smoke)

**Files:** none (verification + a short `docs/superpowers/specs/2026-09-14-situation-room-ux-design.md` status note).

**Steps:**
- [ ] Backend: `uv run pytest tests/org -q` (+ integration store/collaboration/audit/timeline tests with the
  testcontainers env) all green; `uv run ruff check app/org`; `uv run mypy app/org`; `uv run alembic upgrade head`
  clean; `uv run python scripts/export_openapi.py` committed.
- [ ] Frontend: `npm run typecheck && npm run test -- src/features/org && npm run build` all green.
- [ ] Browser smoke (preview tools): start the FE dev server, open an org page, confirm the Situation Room
  surfaces render without console errors; capture a screenshot.
- [ ] Update the spec doc with an "Implemented (v1)" status note; commit.
