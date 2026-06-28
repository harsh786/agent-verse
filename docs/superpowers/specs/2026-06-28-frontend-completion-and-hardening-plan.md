# Frontend Completion & World-Class Hardening — Master Plan

**Date:** 2026-06-28
**Status:** Verified gap analysis → ready for implementation planning
**Scope:** Close the backend↔frontend gap and elevate the AgentVerse frontend to a world-class,
fully observable, fully auditable autonomous-agent OS. Every claim below is **verified against the
actual code** (file:line). No backend behavior changes except additive, explicitly-noted endpoints.
No regression to existing flows.

> Companion spec: `2026-06-28-agent-civilization-design.md` (the Civilization feature). This plan
> closes the *existing* platform's UI/UX gaps; the two are independent and can proceed in parallel.

---

## 0. How to read this plan

- **§1 Verification ledger** — the user's original gap list, each item marked
  CONFIRMED / REFUTED / PARTIAL with evidence. Build only on what's real.
- **§2 Backend→Frontend coverage map** — authoritative, every router.
- **§3 Complete prioritized gap inventory** — original + newly-discovered, P0/P1/P2.
- **§4 Workstreams** — the actual plan, grouped so dependencies flow correctly.
- **§5 Phasing** — shippable increments; each leaves the tree green.
- **§6 Testing & no-regression gate.**
- **§7 Risks. §8 Success criteria.**

---

## 1. Verification Ledger (original analysis vs. code)

### 1.1 Cross-cutting bugs

| # | Claim | Verdict | Evidence | Correction |
|---|---|---|---|---|
| Bug1 | localStorage/sessionStorage mismatch in 4 pages | **PARTIAL** | `OnboardingPage.tsx:242` uses raw `localStorage.getItem("av_api_key")`. `SimulationPage.tsx:24`, `AuditExplorerPage.tsx:10`, `RpaLivePage.tsx:13` correctly use `sessionStorage ?? localStorage`. Store is sessionStorage-primary (`auth.ts:39-40`). | **Only 1 page is buggy**, not 4. |
| Bug2 | 3 agent pages use raw fetch, bypass `agentsApi` | **CONFIRMED** | `AgentsListPage.tsx:17-22`, `AgentCreatePage.tsx:9-20`, `AgentDetailPage.tsx:23-68`. `agentsApi` (client.ts:166-181) already has snapshot/rollback/export/listVersions. | — |
| Bug3 | API path mismatches | **CONFIRMED — but the bug is in `client.ts`, not the pages** | Backend (verified) serves `GET /analytics/costs` (`analytics.py:76`, prefix `/analytics`) and `POST /nl/schedule` (`schedules.py:249`, prefix `/nl`). So the **pages are correct** (`AnalyticsDashboard.tsx:26`→`/analytics/costs`, `SchedulesPage.tsx:442`→`/nl/schedule`) and the **client methods are wrong**: `analyticsApi.getCostMetrics`→`/analytics/cost` (client.ts:434) and `schedulesApi.createNl`→`/schedules/nl` (client.ts:410). `CostDashboard.tsx:23`→`/goals/cost-metrics` is a **real, correct endpoint** (`goals.py:151`) — not a bug, just untyped. | Fix the two wrong client paths (Phase-1 Task 1). |
| Bug4 | `GovernancePage.approve` missing `approver` | **CONFIRMED — real 422** | Backend `ApproveRejectRequest` requires `approver: str` (`governance.py:36`); `GovernancePage.tsx:211-227` posts only `{note}` via local `apiFetch` → request 422s. Approvals are broken today. | Send `approver` (from auth) — Phase-1 Task 4. |
| Bug5 | `navigate(/goals/${res.goal_id})` can be undefined | **CONFIRMED** | `GoalsListPage.tsx:58`; `GoalResponse.goal_id?` optional, `id` mandatory (client.ts:76-85). | Use `res.goal_id ?? res.id`. |

### 1.2 Unused typed-client methods — **all 10 CONFIRMED present-but-uncalled**

`goalsApi.submitBatch` (119), `goalsApi.pause` (124), `goalsApi.resume` (126),
`goalsApi.getEventLog` (130), `goalsApi.getEvaluation` (133), `tenantsApi.signup` (250),
`governanceApi.getPendingApprovals` (315), `memoryApi.recall` (450), `memoryApi.store` (452),
`analyticsApi.getEvalMetrics` (436). (Line numbers in `src/lib/api/client.ts`.)

### 1.3 Orphan pages (route exists, not in sidebar) — **CONFIRMED**

Routes present in `App.tsx` but absent from `Sidebar.tsx` nav: `/simulation`, `/audit`,
`/rpa/live`, `/onboarding`, `/connectors/catalog`, plus dynamic detail routes `/goals/:goalId`,
`/agents/:agentId`, `/agents/create` (detail routes are expected to be deep-linked, not
navigated — they are not "orphans" in the problematic sense; `/simulation`, `/audit`,
`/rpa/live`, `/connectors/catalog`, `/onboarding` are).

### 1.4 Stub — WorkflowBuilder — **CONFIRMED**

`WorkflowBuilderPage.tsx:268-279` renders nodes as a vertical `<div>` flex list; `edges[]`
computed (lines 69, 125) but never rendered; **no `@xyflow/react` import anywhere** in the file
(despite being a dependency). It is a form masquerading as a graph editor.

### 1.5 Partial pages — **all CONFIRMED**, key nuances

- **GoalDetailPage**: has SSE/HITL/timeline/inspector; missing eval scorecard, completed-goal
  replay (`goalsApi.getEventLog` unused), traces view, pause/resume (only cancel at line 237).
- **KnowledgePage**: `SOURCE_TYPES = ['text','markdown','git','openapi']` (line 220) — 4 sources.
  File upload `accept` includes `.pdf,.docx` (line 268) but they are **not** selectable ingest
  source types. Missing: URL, PDF, DOCX, GitHub, Confluence, Jira, Slack.
- **ObservabilityPage**: `GRAFANA_URL='http://localhost:3001'` hardcoded (line 7); no Jaeger
  link, no span viewer (despite `/observability/spans` existing).
- **CostDashboardPage**: KPI cards + daily bar only; no cost-over-time, no cost-by-model.
- **AnalyticsDashboardPage**: goals-by-status + tool charts only; no eval-metrics chart
  (`analyticsApi.getEvalMetrics` unused), no agent analytics, no time-period selector.
- **ConnectorsCatalogPage**: Register button navigates without passing catalog item (line 85).
- **EvalPage**: red-team/simulation/per-goal scorer present; no eval-suite CRUD.
- **AgentDetailPage**: edit/test/snapshot/export/versions/readiness present; missing
  credentials, permissions, clone, knowledge assignment, rollout gate.
- **EnterprisePage**: export/delete/residency present; no async-export polling, no consent mgmt.
- **DashboardPage**: KPI cards + activity feed; no charts, no agent-health grid, no approvals widget.
- **SchedulesPage**: webhook trigger selectable (line 71) but renders **no config fields**
  (URL/secret) — conditional fields only for cron/interval (lines 270-296).

---

## 2. Backend → Frontend Coverage Map (authoritative)

| Router | Coverage | Action class |
|---|---|---|
| goals, agents, governance, tenants | **FULL** | Enhance/complete partials |
| analytics, connectors, knowledge, schedules, enterprise, collab, rpa, replay, civilization | **PARTIAL** | Complete features + wire unused endpoints |
| **auth (Keycloak)** | **NONE** | New SSO flow |
| **a2a** | **NONE** | New observability dashboard (read-only) |
| **artifacts** | **NONE** | New artifacts browser |
| **integrations** | **NONE** | New integrations config |
| **training_export** | **NONE** | New export action UI |
| **tools** | **NONE** | New code-runner + file-manager + email composer |
| **memory** | **CLIENT-ONLY** | New memory explorer |
| **perception** | **NONE** | New perception/vision UI |
| system | N/A | server-only (health/metrics already surfaced) |

---

## 3. Prioritized Gap Inventory

### P0 — blocks "world-class / safe to operate"

| ID | Gap | Evidence |
|---|---|---|
| P0-1 | **Emergency-stop kill switch has no UI** | `/governance/emergency-stop` `governance.py:584-705` — no caller in `src/` |
| P0-2 | **No client session-expiry / token-refresh handling** (silent 401s) | `auth.ts` has no refresh; `/auth/refresh` `auth.py:108` unused |
| P0-3 | **Bug fixes**: Onboarding storage (Bug1), agent pages raw-fetch (Bug2), 3 path mismatches (Bug3), goal_id fallback (Bug5), reconcile `governanceApi.approve` (Bug4) | §1.1 |
| P0-4 | **Keycloak SSO flow unreachable** (entire `/auth/*` blackout) | `auth.py:27-139`; `AuthPage` `signup` href is dead |
| P0-5 | **UX foundations**: global toast system, app-wide `ErrorBoundary`, 401→logout interceptor | no toast service found; `ErrorBoundary.tsx` not wrapping pages |

### P1 — high-value completeness

| ID | Gap | Evidence |
|---|---|---|
| P1-1 | RBAC: team/role management + IP-allowlist UI | `tenants.py:267-375`, `:396-528`, `rbac.py` |
| P1-2 | Notification center + channel config + delivery logs | `governance.py:524-553`, `NotificationService` |
| P1-3 | Real-time approvals: SSE push + TopBar live counter (replace polling) | `/governance/approvals` GET-only; pub/sub exists |
| P1-4 | Memory explorer (list/recall/delete, tool-reliability) | `memory.py:33-203`; `memoryApi` client-only |
| P1-5 | Artifacts browser (list/preview/download/delete) | `artifacts.py:18-104` |
| P1-6 | Tools UIs: code runner, file manager, email composer | `tools.py:27-134` |
| P1-7 | Integrations config (Slack/Zapier/AlertManager/Datadog) + test triggers | `integrations.py:25-420` |
| P1-8 | Training-data export action UI (JSONL, OpenAI/Anthropic) | `training_export.py:23` |
| P1-9 | GDPR async-export polling + consent mgmt + legal-hold status | `enterprise.py:451-558`, `governance.py:812-836` |
| P1-10 | API-key rotation wizard + BYOK vault-key UI | `tenants.py:142-173`, `:469-492` |
| P1-11 | Billing/usage/plan-limits dashboard + feature-gate indicators | `governance.py:489-518`, `analytics.py` cost |
| P1-12 | **World-class visual Workflow Builder** — replace the form-masquerading-as-graph with a real drag-and-drop builder (adopt `@xyflow/react`). Full design in **WS-8** | §1.4; `WorkflowBuilderPage.tsx` is a vertical input-card list, `edges` never rendered, no save/load, "run" only POSTs goal text |
| P1-13 | Knowledge ingest depth (URL/PDF/DOCX/GitHub/Confluence/Jira/Slack) | `KnowledgePage.tsx:220` |
| P1-14 | GoalDetail: eval scorecard + completed-goal replay + pause/resume + traces | §1.5 |
| P1-15 | Webhook trigger config fields in SchedulesPage | `SchedulesPage.tsx:270-296` |

### P2 — polish & depth

| ID | Gap | Evidence |
|---|---|---|
| P2-1 | Perception/vision UI (screenshot/analyze/extract) | `perception.py` |
| P2-2 | A2A observability dashboard (read-only task list/cards) | `a2a.py:149-260` |
| P2-3 | Decision-trace explainability viewer | `intelligence.py:17-37` (needs read endpoint) |
| P2-4 | Marketplace versioning/changelog/rollback UI | `enterprise.py:232-266` |
| P2-5 | Self-optimization suggestion rationale + batch apply | `enterprise.py:302-339` |
| P2-6 | CostDashboard time-series + by-model; Analytics time-period selector + eval/agent charts | §1.5 |
| P2-7 | Dashboard charts + agent-health grid + approvals widget | §1.5 |
| P2-8 | AgentDetail: credentials, permissions, clone, knowledge assignment, rollout gate | §1.5 |
| P2-9 | Loading skeletons + optimistic updates across pages | basic "Loading…" text only |
| P2-10 | Accessibility pass (ARIA, focus mgmt, keyboard nav) | ~24 a11y attrs total |
| P2-11 | Responsive/mobile layout | minimal `md:`/`lg:` usage |
| P2-12 | Global search (replace static command palette) | `CommandPalette.tsx` static array |
| P2-13 | Sidebar links for orphan pages (`/simulation`,`/audit`,`/rpa/live`,`/connectors/catalog`); fold `/simulation` into `/eval` to remove the duplicate | §1.3 |
| P2-14 | Frontend test coverage to ≥80% of feature pages; expand Playwright e2e | ~37% pages untested |

### Entity detail views & dashboards (completeness — verified gap category)

Only **3 of ~20 entities** have a detail/drill-down route (`App.tsx:99-125`: `goals/:goalId`,
`agents/:agentId`, `civilization/:id`). Everything else is a flat list or tab group with no place
to see full details, history, related entities, or per-item actions. Likewise only 4 thin
dashboards exist (Dashboard, Cost, Analytics, Observability) and none are entity-scoped.

| ID | Gap | Severity | Evidence |
|---|---|---|---|
| DV-1 | **No detail/drill-down pages** for: connector, schedule, knowledge collection + document, policy, approval request, collaboration session, marketplace template, eval run + suite, audit event, RPA session, memory entry, artifact | P1 | `App.tsx:99-125` (only 3 `:id` routes); each feature is list/tab-only |
| DV-2 | **Connector discovery/freshness**: catalog shows no "newly added"/recency, no health/status, not sortable/filterable; no per-connector detail (tools exposed, auth type, health history, **which agents/goals use it**, last-tested) | P1 | `ConnectorsCatalogPage.tsx` (no date/status/new badge); `ConnectorsRegisteredPage.tsx:15,27` has `status` but no detail view |
| DV-3 | **No per-entity dashboards**: agent dashboard (success rate, latency, cost, recent goals, health), connector-health dashboard, schedule run-history/next-fire, knowledge usage; the 4 existing dashboards are thin (overlaps P2-6/P2-7) | P1 | only `Dashboard/CostDashboard/Analytics/Observability`; no agent/connector/schedule dashboards |
| DV-4 | **Detail pages lack cross-links & history**: goal→agent→connectors→knowledge→audit→cost should interlink; entities should show their event/version/run history and "used by / depends on" relationships | P2 | `GoalDetailPage`/`AgentDetailPage` show config but no relationship graph or cross-navigation |
| DV-5 | **"What's new" / recency surfaces**: newly added connectors, recently created agents/goals/schedules, recent marketplace templates — no recency badges or activity-scoped views anywhere | P2 | no `created_at`-sorted or "new" badge usage found in catalog/list pages |

---

## 4. Workstreams

Ordered so shared foundations land first and later work reuses them. Each workstream lists the
files it touches and the **typed-client / endpoints** it consumes (reusing what already exists).

### WS-0 — Foundations (must land first; everything reuses these)
1. **Bug sweep** (P0-3): fix Onboarding storage → `useAuthStore`; migrate `AgentsList/Create/Detail`
   to `agentsApi`; fix 3 path mismatches (add `analyticsApi.getCostMetrics` alias + a real
   `/analytics/cost` for CostDashboard, fix `createNl` path usage); `goal_id ?? id`; route all
   approval calls through a single `governanceApi.approve(requestId, approver, note)` (pull
   `approver` from `useAuthStore`).
2. **Toast system** (P0-5): `src/components/ui/Toast.tsx` + `useToast` store; wire into
   `client.ts` request layer for error toasts.
3. **App-wide ErrorBoundary** (P0-5): wrap each route element in `App.tsx`.
4. **401 interceptor + session handling** (P0-2): in `client.ts` `request()`, on 401 →
   attempt `/auth/refresh` (if SSO) else `logout()` + redirect; add a session-expiry warning modal.
5. **Skeleton + EmptyState + StatusBadge primitives** (P2-9): shared in `src/components/ui/`.
6. **Reusable graph canvas** (`src/components/graph/FlowCanvas.tsx`) wrapping `@xyflow/react` —
   shared by WorkflowBuilder (P1-12) and the Civilization map (companion spec).

### WS-1 — Auth & Security (P0/P1)
- SSO flow: `/auth/login` redirect, code→token exchange callback page, `/auth/userinfo` hydration,
  refresh loop; reachable login button on `AuthPage`; wire `tenantsApi.signup` to the dead link.
- API-key rotation wizard + BYOK vault-key form in `SettingsPage` (P1-10).
- Emergency-stop control in `TopBar` (confirm modal + live status banner) (P0-1).

### WS-2 — Governance & Real-time (P1)
- Notification center page + channel CRUD + delivery logs (P1-2).
- Approvals SSE stream + TopBar live counter; auto-updating approvals page (P1-3).
- Legal-hold status + GDPR async-export polling + consent management (P1-9).
- Audit explorer: typed model, date/tool/outcome filters, CSV/JSON export (replaces `any[]` stub).
- RBAC: roles + IP-allowlist management pages (P1-1).

### WS-3 — Blackout routers → first-class pages (P1/P2)
- Memory explorer (P1-4), Artifacts browser (P1-5), Tools (code runner/file manager/email) (P1-6),
  Integrations config (P1-7), Training-data export (P1-8), Perception UI (P2-1),
  A2A observability (P2-2).

### WS-4 — Complete partial pages (P1/P2)
- WorkflowBuilder → see dedicated **WS-8** (flagship; too large for this workstream).
- Knowledge ingest source expansion (P1-13).
- GoalDetail eval scorecard + replay + pause/resume + traces (P1-14); wire `goalsApi.pause/resume/
  getEventLog/getEvaluation`.
- SchedulesPage webhook config fields (P1-15); ConnectorsCatalog Register pre-fill.
- AgentDetail advanced tabs (P2-8); Eval-suite CRUD (P2 from EvalPage).

### WS-5 — Dashboards & analytics depth (P2)
- CostDashboard time-series + by-model; Analytics time-period selector + eval/agent charts
  (`getEvalMetrics`); Dashboard charts + agent-health grid + approvals widget. (P2-6, P2-7)

### WS-6 — Platform polish (P2)
- Billing/usage/plan UI (P1-11) [value-high but no billing backend — surfaces limits + usage only;
  flag if a billing service is later added].
- Global search (P2-12); orphan-page nav + `/simulation`→`/eval` consolidation (P2-13);
  decision-trace viewer (P2-3, needs a small additive read endpoint); marketplace versioning (P2-4);
  self-opt rationale (P2-5).
- Accessibility pass (P2-10); responsive pass (P2-11).

### WS-7 — Entity detail pages & dashboards (DV-1…DV-5)
The "details aren't listed / dashboards missing" gap. Introduces a **reusable detail-page shell**
(`src/components/detail/DetailLayout.tsx`: header + status + actions + tabbed sections + relationship
rail) and a **dashboard kit** (KPI row + recharts panels + recency feed) so every entity page is
consistent and quick to build.
- **Detail/drill-down routes** (DV-1): add `:id` routes + pages for connector, schedule,
  knowledge-collection (+document), policy, approval, collaboration session, marketplace template,
  eval run + suite, audit event, RPA session, memory entry, artifact. Each shows full record,
  history (versions/runs/events), per-item actions, and cross-links (DV-4).
- **Connector experience** (DV-2): catalog gets recency ("new" badge by `created_at`), health/status,
  search/sort/filter; per-connector detail lists exposed tools, auth type, last-tested, health
  history, and **which agents/goals consume it** (reverse lookup). Register pre-fills from catalog.
- **Entity dashboards** (DV-3): Agent dashboard (success rate, latency, cost, recent goals, health),
  Connector-health dashboard, Schedule run-history/next-fire, Knowledge usage — built from the
  dashboard kit, reusing `analyticsApi` + per-entity endpoints. Folds in P2-6/P2-7 depth.
- **Recency surfaces** (DV-5): "what's new" widgets on Dashboard + list pages (newly added
  connectors, recent agents/goals/schedules/templates) using `created_at` sorting.

> Detail pages reuse existing per-entity GET endpoints where present; where an entity lacks a
> single-item GET (e.g. a specific schedule), it is served from the list response client-side until
> a thin additive `GET /{entity}/{id}` is added (flagged per-entity in the Phase plan, backend-owned).

### WS-8 — World-class Visual Workflow Builder (flagship; expands P1-12)

**Problem.** Today's `WorkflowBuilderPage` is a vertical list of text-input cards: `edges` are
computed but never drawn, `@xyflow/react` is never imported, there is no persistence, and "Run"
just POSTs the goal text to `/goals`. The backend already has `WorkflowPlanner`
(`workflow_planner.py:126` → `WorkflowPlan.execution_waves()` for parallelism) and
`WorkflowExecutor` (`workflow_executor.py:27`), but **no workflow persistence or run API**.

**Goal.** A genuinely good, easy-to-use visual builder: drag-drop nodes, draw connections,
configure each node inline, validate, save/version, generate from natural language, and watch it
execute live on the canvas — comparable to best-in-class flow editors (n8n / Make / Zapier-grade).

**8.1 Canvas & interaction (built on the WS-0 `FlowCanvas`)**
- `@xyflow/react`: pan/zoom, **MiniMap**, **Background grid** with snap-to-grid, fit-view,
  zoom controls.
- **Drag-and-drop** nodes from a left **Node Palette** (searchable, categorized) onto the canvas.
- **Connect by dragging** from a node's output handle to another's input handle; invalid
  connections rejected with a reason tooltip.
- **Auto-layout** (dagre) button to tidy the graph; manual positions persisted.
- **Undo/redo**, copy/paste, multi-select + box-select, delete, duplicate; full **keyboard
  shortcuts** with a discoverable cheatsheet (`?`).
- **Autosave** (debounced) + explicit Save; dirty-state indicator.

**8.2 Node types** (color-coded, icon, status ring)
Trigger/Start, **Tool Call** (pick MCP connector → tool from the catalog; reuses connector data),
**Agent Step** (assign an agent), **Decision/Branch** (condition → labeled true/false edges),
**Parallel/Fan-out** (maps to `execution_waves`), **Loop/Map** (iterate over a collection),
**Human Approval** (HITL gate, reuses governance), **Sub-workflow** (embed another saved workflow),
**Delay/Wait**, End. Each renders as a custom React Flow node component.

**8.3 Node Inspector (right panel)**
Per-node config: tool/agent selection, **input mapping** from upstream node outputs (a
dropdown/expression picker over available `{{node.output.field}}` references — the data-flow UX),
condition builder for branches, retry/timeout/risk level, and a description. Live validation per
field with inline errors.

**8.4 Validation & guardrails**
Before save/run: detect unreachable nodes, missing required config, dangling edges,
unintended cycles (loops must be explicit Loop nodes), and broken input mappings. Surface as
node error badges + a problems panel; block Run until clean.

**8.5 Generate & templates**
- **NL → workflow**: type a goal → call `WorkflowPlanner` → render the returned `WorkflowPlan`
  as an editable graph (elevates the current "convert plan steps" behavior into the real canvas),
  with parallel waves laid out in lanes.
- **Template gallery**: start from curated templates; **import/export JSON**; publish a workflow
  to the marketplace (reuses marketplace endpoints).

**8.6 Live execution on the canvas**
- **Test/Dry-run** and **Run**: stream execution via SSE (reuse the `useGoalStream` pattern) so
  nodes light up as they run (queued → running → success/failed), edges animate along the active
  path, and per-node output/error is shown in the Inspector. Step-through and pause where the
  backend supports it. A run history list with replay.

**8.7 UX polish**
Empty-state that offers templates or NL-generate; contextual help; responsive split-pane
(palette / canvas / inspector) that collapses gracefully; consistent toasts + skeletons; full
a11y (keyboard reachable nodes, ARIA on controls).

**8.8 Backend (additive — built as part of Phase 6, in scope)**
Phase 6 delivers the **full backend** so Save/Run work end-to-end (not design-only):
- **Persist** workflows as a first-class entity: `workflows` table (tenant-scoped, RLS, additive
  migration following the `0004_goals` pattern) + new `app/api/workflows.py` router with
  `POST/GET/PUT/DELETE /workflows`, list, and **versioning** (snapshots like agents). Models in
  `app/db/models/workflow.py`.
- **Execute**: `POST /workflows/{id}/run` (and `?dry_run=true`) wrapping the existing
  `WorkflowPlanner`/`WorkflowExecutor` (`workflow_planner.py:126`, `workflow_executor.py:27`);
  `GET /workflows/{id}/runs` + an SSE run stream reusing the `GoalService` event/pub-sub pattern.
- **Governance reuse**: workflow runs flow through the same `CostController`, `PolicyEngine`,
  `HITLGateway`, and `AuditLog` as goals (Human-Approval nodes map to the HITL gateway).
- Backend ships with its own pytest unit + integration (testcontainers) coverage; RLS isolation
  asserted. **Design-only mode remains only as a contingency** if the backend slips a sub-phase —
  the frontend feature-detects `/workflows` and disables Save/Run with an explanatory note.

> **Additive backend endpoints required** (explicitly called out so backend stays in control):
> `/governance/approvals/stream` (SSE), `/governance/policies/stream` (SSE),
> `GET /goals/{id}/decision-traces` (read DecisionTrace), and the **workflow persistence + run**
> suite for WS-8 (`/workflows` CRUD + versions, `POST /workflows/{id}/run` + dry-run,
> `GET /workflows/{id}/runs` + SSE). Everything else reuses existing endpoints.

---

## 5. Phasing (shippable increments, tree stays green each phase)

| Phase | Theme | Workstreams | Exit criteria |
|---|---|---|---|
| **1** | Foundations + bug sweep | WS-0 | All §1.1 bugs fixed with tests; toast/ErrorBoundary/401 live; `FlowCanvas` ready; existing suites green |
| **2** | Auth & safety controls | WS-1 + P0-1 | SSO reachable + refresh loop; emergency stop in TopBar; key rotation/BYOK UI |
| **3** | Governance & real-time | WS-2 | Notification center; live approvals via SSE; RBAC + audit export; legal-hold/GDPR |
| **4** | Blackout routers | WS-3 | Memory/Artifacts/Tools/Integrations/Training-export reachable + tested; Perception/A2A read UIs |
| **5** | Complete partials | WS-4 | Goal pause/resume/replay/eval; knowledge ingest depth; schedule webhook config; AgentDetail tabs |
| **6** | World-class Workflow Builder (frontend **+ full backend**) | WS-8 | Real drag-drop canvas; node palette + inspector + validation; NL-generate; templates/import-export; live SSE execution on canvas; **`workflows` table + CRUD/versioning + run/dry-run + run-stream backend (with pytest)** wired through existing governance; Save/Run work end-to-end |
| **7** | Detail pages & entity dashboards | WS-7 | `DetailLayout` + dashboard kit; detail routes for all entities (DV-1); connector experience + reverse-lookup (DV-2); agent/connector/schedule dashboards (DV-3); cross-links + recency (DV-4/DV-5) |
| **8** | Polish & analytics depth | WS-5 + WS-6 | Charts/time selectors; global search; a11y + responsive pass; nav cleanup; coverage ≥80% |

Each phase becomes its **own writing-plans implementation plan**. Phase 1 is the prerequisite for
all others (shared primitives).

---

## 6. Testing & No-Regression Gate

**Per new/changed page:**
- **vitest** component test using the existing pattern (`QueryClientProvider` + `MemoryRouter` +
  `vi.spyOn(globalThis,'fetch')`), covering: render, loading, empty, error, and the primary action.
- **Playwright e2e** using existing `setupAuth` + route-mock helpers (`e2e/*.spec.ts`), covering the
  happy path + one failure path + (where relevant) live SSE/WS updates with mocked streams.

**Targeted suites:**
- Auth: login redirect, token-refresh on 401, logout-on-expiry, emergency-stop confirm flow.
- Real-time: approvals SSE updates the counter and list without reload.
- Blackout routers: each new page round-trips against mocked endpoints.
- Bug regressions: a test per §1.1 fix that fails on the old behavior.
- Workflow Builder (WS-8): unit tests for add/connect/delete nodes, validation errors, NL-generate
  rendering, import/export round-trip; Playwright e2e that drags two nodes, connects them, saves,
  runs (mocked SSE), and asserts node status transitions animate on the canvas.

**No-regression gate (CI):**
- `npm run typecheck` + `npm run lint` + `npm run test` + `npm run test:e2e` all green.
- Backend changes are **additive only** — the SSE/decision-trace endpoints and the Phase-6
  `workflows` suite (table + router + run) listed in §4; no existing table/router/endpoint is
  modified. Backend `uv run pytest`/`ruff`/`mypy` stay green; every new endpoint and the
  `workflows` migration ship with their own pytest unit + integration coverage.
- Coverage does not decrease; target ≥80% of feature pages with a test by end of Phase 8.

---

## 7. Risks & Mitigations

| Risk | Mitigation |
|---|---|
| Large surface → regressions | Phase 1 foundations + per-page tests + CI gate; ship phase-by-phase behind nav, not big-bang |
| SSO flow complexity (Keycloak) | Reuse `/auth/*` endpoints as-is; client only orchestrates redirect+exchange+refresh; feature-detect via `/auth/config` |
| Real-time SSE load | Reuse `useGoalStream` reconnect/backoff pattern; additive SSE endpoints only |
| "Billing" has no backend | Scope P1-11 to surfacing existing budget/usage limits; defer true billing until a service exists |
| Duplicate Simulation/Eval pages | Consolidate `/simulation` into `/eval`; redirect old route |
| Touching shared `client.ts` breaks callers | Add methods/aliases, don't rename; cover with type-check + tests |

---

## 8. Success Criteria

1. **Zero UI blackouts**: every backend router (except server-only `system`) is reachable and
   useful from the UI; every one of the 10 unused typed-client methods is either wired or removed.
2. **Operable & safe**: emergency stop, session refresh/expiry, RBAC, and notifications all work
   from the UI; no silent 401s.
3. **Everything auditable & visible**: audit export/filter, live approvals, memory explorer,
   artifacts, decision traces, and dashboards give clear, real-time, exportable views.
4. **No fakes**: WorkflowBuilder is a real, drag-and-drop visual builder (palette, inspector,
   validation, NL-generate, save/version, and live on-canvas execution) — not a form; no stubbed
   `any[]` pages remain.
5. **Quality bar**: toasts, error boundaries, skeletons, a11y, and responsive behavior are
   consistent; ≥80% of feature pages have tests; all existing + new suites pass; no regressions.
6. **Detail & dashboard completeness**: every major entity (connector, schedule, knowledge,
   policy, approval, collab session, marketplace template, eval, audit event, RPA session, memory,
   artifact — plus goals/agents/civilization) has a drill-down detail page with full record,
   history, actions, and cross-links; agent/connector/schedule dashboards exist; newly-added
   connectors and recent entities are surfaced with recency badges.