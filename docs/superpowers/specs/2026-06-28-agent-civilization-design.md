# Agent Civilization — Design Specification

**Date:** 2026-06-28
**Status:** Approved design → ready for implementation planning
**Scope:** A persistent, tenant-scoped society of AI agents that autonomously spawn,
communicate, debate, and learn collectively to execute goals — built **on top of** the
existing AgentVerse platform, additively, behind a feature flag, with zero regression to the
current system.

---

## 1. Vision & North Star

An **Agent Civilization** is a long-lived society of agents owned by one tenant. A goal enters
the society; agents within it **autonomously spawn and coordinate other agents** ("no human in
the loop" for day-to-day work), debate high-stakes decisions, share findings on a blackboard,
and promote validated learnings into shared memory. Agents accrue reputation, and weak/idle
agents are retired. Everything is **observable, auditable, and replayable** through a
world-class UI.

Autonomy is bounded by a **Constitution** — hard limits enforced by a central **Governor** —
so the society is autonomous in the normal case and human-gated only at hard edges
(high-risk actions or guardrail breaches).

### 1.1 Locked design decisions

| Decision | Choice |
|---|---|
| Autonomy boundary | **Autonomous within hard guardrails** (Constitution + Governor; HITL only on high-risk actions or breaches) |
| Lifecycle/scope | **Persistent standing society per tenant** — agents born, live, accrue reputation, retired |
| Communication | **Shared Redis event bus + blackboard**, reusing the A2A data model for direct agent-to-agent tasks |
| Collective learning | **Curated promotion** — candidate pool → `EvalRunner` validation → promote to `LongTermMemoryStore` |
| Spawn authority | **Executing agent via a governed `spawn()` tool**; Governor enforces the Constitution centrally |
| Society mechanics | **Eval-driven reputation + Governor auto-retire** |

### 1.2 Non-goals (YAGNI)

- No cross-tenant civilizations (strict per-tenant isolation).
- No new LLM provider work; reuses the existing provider abstraction.
- No custom agent runtime; reuses the existing LangGraph `AgentLoop`/`AgentGraph`.
- No economic/token simulation beyond USD cost budgets.
- No agent "emotion/personality" modeling.
- No autonomous code-deployment by agents beyond what existing tools + HITL already allow.

---

## 2. Reuse Map (what we build ON, verified against current code)

The civilization is mostly an **orchestration + persistence + governance + observability layer**.
The following existing components are composed as-is or extended additively:

| Existing component | File | How the civilization uses it |
|---|---|---|
| `SupervisorAgent.run()` | `app/agent/supervisor.py:66` | Hierarchical decompose → spawn → synthesize (one spawning strategy) |
| `DebateOrchestrator.run()` | `app/agent/debate.py:50` | High-stakes / conflict resolution; returns `DebateResult` with `consensus_level` |
| `AgentCollabSession` + `CollaborationStore` | `app/collab/agent_collab.py`, `app/collab/store.py:66` | Consensus rounds; RLS + optimistic concurrency (`append_operation`, `VersionConflictError`) |
| A2A task/result model + HMAC | `app/api/a2a.py`, `app/mcp/a2a.py` | **Data model + HMAC only** for cross-agent tasks. NOT the public ingress (see §6.3). |
| `AgentRouter.route()` | `app/agent/router.py:175` | Route an incoming goal to a society member; `mode=needs_new_agent` triggers spawn |
| `MetaAgentPlanner.plan()` | `app/intelligence/meta_agent.py:56` | Generate a new agent config for a capability gap (validated by Governor before persist) |
| `EvalRunner.score_and_persist()` | `app/intelligence/eval_runner.py:121` | Score task outcomes → feeds reputation + learning validation |
| `LongTermMemoryStore` | `app/memory/long_term.py:31` | Destination for **promoted** collective learnings |
| `CostController` / `RedisCostController` | `app/governance/cost.py:30,120` | Budget enforcement; extended with parent-goal rollup (see §5.2) |
| `PolicyEngine.evaluate(tool, ctx, parent_policy_ids)` | `app/governance/policies.py:85` | Per-tool policy; supports policy inheritance for child agents |
| `HITLGateway` | `app/governance/hitl.py:108` | Human gate on high-risk actions / breaches |
| `AuditLog.record()` | `app/governance/audit.py:58` | Append-only trail; extended with `civilization_id` + `parent_agent_id` |
| Celery per-plan queues | `app/scaling/celery_app.py`, `tasks.py` | Run spawned agents as background tasks across workers |
| `GoalService` SSE + Redis pub/sub | `app/services/goal_service.py` | Pattern mirrored for civilization event streaming |
| `Goal.parent_goal_id` (self-ref FK) | `app/db/models/goal.py:26` | Existing home for spawn lineage |
| LangGraph checkpointer | `app/agent/graph.py`, `app/main.py:443` | Resume spawned-agent state across replicas |

### 2.1 Safety findings the design MUST resolve (from code analysis)

1. **No depth/cost containment on recursion.** `AgentLoop.run()` can recurse with no depth limit;
   `CostController` is per-goal and does not roll sub-agent cost up to the parent. → The Governor +
   Constitution own depth tracking, parent-goal cost rollup, and exponential per-depth budget decay.
2. **A2A public ingress hardcodes `A2A_TENANT_ID`** → using it for internal spawning bypasses
   per-tenant budget/isolation. → Internal spawning uses a **tenant-scoped Celery dispatch path**,
   not the A2A HTTP ingress.
3. **`MetaAgentPlanner` output is unvalidated** (incl. `autonomy_mode`). → Governor runs
   `MetaAgentConfig.validate()` before any agent is persisted.

---

## 3. System Architecture

```
                         ┌──────────────────────────────────────────────┐
                         │              Civilization (per tenant)         │
   Goal ──► AgentRouter ─┤  Governor (Constitution enforcement)           │
                         │     ▲ spawn()/deny    │ auto-retire            │
                         │     │                 ▼                        │
                         │  Society (members + reputation + lineage)      │
                         │     │            │             │               │
                         │  Agent A ──────► Agent B ────► Agent C  (LangGraph loops, Celery) │
                         │     │  bus msgs  │   debate    │               │
                         │     ▼            ▼             ▼               │
                         │  Event Bus (Redis pub/sub) + Blackboard (PG)   │
                         │            │                                   │
                         │  Learning pipeline: candidates → EvalRunner →  │
                         │            promote → LongTermMemoryStore        │
                         └───────────────┬────────────────────────────────┘
                                         │  events (SSE/WS) + audit + metrics + replay
                                         ▼
                              World-class Civilization UI
```

### 3.1 New backend package `app/civilization/`

| Module | Responsibility | Primary collaborators |
|---|---|---|
| `constitution.py` | `Constitution` model + `evaluate_spawn()` / `evaluate_breach()` | `CostController`, `PolicyEngine` |
| `governor.py` | Central authority: approve/deny spawns, validate configs, auto-retire, kill switch | `MetaAgentPlanner`, `AgentStore`, `AuditLog`, `Constitution` |
| `spawn_tool.py` | Governed `spawn(capability, goal, ...)` native tool exposed to agents | tool registry, `Governor` |
| `bus.py` | `CivilizationBus` — Redis pub/sub topics + broadcast; persists to `bus_messages` | Redis, `EventStore` |
| `blackboard.py` | `Blackboard` — tenant-scoped shared findings (PG + Redis cache, RLS) | `app/db/rls.py` |
| `society.py` | `Society` — lifecycle, membership, reputation accrual, route-into-society | `AgentRouter`, `EvalRunner` |
| `learning.py` | `LearningPipeline` — candidate pool → curated promotion | `EvalRunner`, `LongTermMemoryStore` |
| `orchestrator.py` | `CivilizationOrchestrator` — ticks the society, enqueues agent Celery tasks, emits events | `CeleryGoalTaskQueue`, `GoalService` |
| `events.py` | Civilization event types + SSE/Redis dispatch (mirrors `GoalService`) | Redis pub/sub |
| `models.py` (in `app/db/models/civilization.py`) | SQLAlchemy models (see §4) | — |

### 3.2 Component contracts (one purpose each, testable in isolation)

- **Constitution**: pure policy object. `evaluate_spawn(ctx) -> SpawnVerdict`,
  `evaluate_breach(metrics) -> BreachVerdict`. No I/O. Fully unit-testable.
- **Governor**: the only component that may create or retire a society member. Stateless across
  calls except via DB/Redis; every decision audited. Depends on Constitution, MetaAgentPlanner,
  AgentStore, CostController, PolicyEngine, AuditLog.
- **CivilizationBus**: publish/subscribe over Redis channels `civ:{tenant}:{civ_id}:{topic}`;
  persists every message to `bus_messages` for replay. No business logic.
- **Blackboard**: append + query findings, RLS-scoped. Optimistic concurrency for updates
  (reusing the `CollaborationStore` pattern).
- **Society**: membership + reputation bookkeeping; reputation is derived from `EvalRunner` scores.
- **LearningPipeline**: candidate → validated → promoted state machine; the only writer of
  civilization-sourced entries into `LongTermMemoryStore`.
- **CivilizationOrchestrator**: the runtime loop; the only component that enqueues agent tasks.

---

## 4. Data Model (additive migrations, all RLS tenant-scoped)

New models in `app/db/models/civilization.py`. Migration follows the `0004_goals` / `0009_intelligence`
pattern: `ENABLE` + `FORCE ROW LEVEL SECURITY` + `CREATE POLICY ... USING (tenant_id = current_setting('app.tenant_id', true))`,
plus indexes on `tenant_id` and FKs. All migrations are reversible (`upgrade`/`downgrade`).

| Table | Key columns | Notes |
|---|---|---|
| `civilizations` | `id`, `tenant_id`, `name`, `status` (`active\|paused\|retired`), `constitution` (JSONB), `created_at`, `updated_at` | One standing society per tenant by default; multiple allowed |
| `civilization_agents` | `id`, `civilization_id`, `tenant_id`, `agent_id`, `role`, `parent_agent_id` (self-ref FK, lineage), `reputation` (float, default 0.5), `status` (`active\|idle\|retired`), `depth` (int), `spawned_at`, `retired_at` | `depth` enforces Constitution max-depth |
| `spawn_requests` | `id`, `civilization_id`, `tenant_id`, `requester_agent_id`, `requested_capability`, `goal_text`, `decision` (`approved\|denied`), `reason`, `verdict` (JSONB: budget/depth/policy snapshot), `created_agent_id` (nullable), `created_at` | Full audit of every spawn attempt |
| `blackboard_entries` | `id`, `civilization_id`, `tenant_id`, `author_agent_id`, `topic`, `content`, `confidence` (float), `refs` (JSONB), `version` (int), `created_at` | Optimistic-concurrency updates |
| `bus_messages` | `id`, `civilization_id`, `tenant_id`, `from_agent_id`, `topic`, `payload` (JSONB), `ts` | Persisted for replay; live traffic on Redis |
| `civilization_learnings` | `id`, `civilization_id`, `tenant_id`, `candidate` (text), `source_agent_id`, `status` (`candidate\|validated\|promoted\|rejected`), `eval_score` (float), `promoted_memory_id` (nullable FK), `created_at`, `decided_at` | Curated-promotion ledger |
| `civilization_events` | `id`, `civilization_id`, `tenant_id`, `type`, `payload` (JSONB), `ts` | Durable event log for SSE replay/reconnect (addresses pub/sub having no replay buffer) |

No existing table is altered except **additive, nullable** columns on `audit_logs`
(`civilization_id`, `parent_agent_id`) — backfilled NULL, safe for the immutability trigger
(it blocks UPDATEs, not the additive migration's column add).

---

## 5. The Governor & Constitution (safety core)

### 5.1 Constitution (per-civilization JSONB, editable in UI)

```
Constitution = {
  max_depth: int,                 # spawn-tree depth cap (default 4)
  max_total_agents: int,          # lifetime cap (default 50)
  max_concurrent_agents: int,     # active at once (default 10)
  total_budget_usd: float,        # whole-civilization cap
  per_agent_budget_usd: float,    # default per spawned agent
  budget_decay: float,            # child_budget = parent_budget * decay^depth (default 0.6)
  spawn_rate_limit_per_min: int,  # dead-man switch (default 20)
  high_risk_requires_hitl: bool,  # default true
  inherited_policy_ids: [str],    # policies every member inherits
  autonomy_ceiling: str,          # max autonomy any member may have (clamps MetaAgentPlanner)
}
```

### 5.2 Governor responsibilities

1. **Spawn enforcement** — on every `spawn()` tool call:
   - Check `depth < max_depth`, `total_agents < max_total_agents`,
     `concurrent < max_concurrent_agents`, spawn-rate within limit.
   - Check remaining civilization budget via `CostController`; compute the child budget as
     `parent_budget × budget_decay^depth` and ensure it fits within the civilization total.
   - Reuse an existing idle member if one matches the capability (`AgentRouter`); otherwise call
     `MetaAgentPlanner.plan()` → `MetaAgentConfig.validate()` (clamp to `autonomy_ceiling`, strip
     disallowed connectors, attach `inherited_policy_ids`) → `AgentStore.create()`.
   - **Parent-goal cost rollup**: spawned agent's goal gets `parent_goal_id` set; a
     `CostAggregator` (extension of `CostController`) attributes child cost up the chain so the
     civilization total is accurate.
   - Record a `spawn_requests` row + `AuditLog` event regardless of approve/deny.
2. **Breach handling** — a background check (Celery beat, every 30s) evaluates live metrics
   against the Constitution. On breach (budget exceeded, runaway spawn rate): **auto-pause** the
   civilization, emit a `civilization_paused` event, and raise a HITL approval to resume.
3. **Auto-retire** — periodically retire members whose reputation < floor or that have been idle
   beyond a TTL, keeping the society bounded. Reputation floor and idle TTL are Constitution-derived.
4. **Kill switch** — `pause()`, `resume()`, `kill_agent(agent_id)`, `throttle(rate)`,
   `adjust_budget(...)`. `pause()` stops all new spawns and signals running agents to halt at their
   next checkpoint.

### 5.3 HITL integration

HITL fires only when: (a) an agent hits an existing high-risk action (`deploy/delete/prod` per
`app/agent/loop.py:45`), or (b) the Governor detects a Constitution breach. Normal spawning and
coordination need no human.

---

## 6. Communication

### 6.1 Event bus

`CivilizationBus` over Redis pub/sub. Topics: `spawn`, `findings`, `debate`, `coordination`,
`lifecycle`. Agents publish/subscribe by topic. Every message is persisted to `bus_messages`
and emitted as a `civilization_events` row so the UI can replay and reconnecting clients can
catch up (Redis pub/sub alone has no replay buffer — `civilization_events` is the durable log).

### 6.2 Blackboard

Tenant-scoped shared store of findings (`blackboard_entries`). Agents post findings with a topic
and confidence; others query before acting (reduces duplicate work). Conflicting high-confidence
claims on the same topic trigger a debate (§7).

### 6.3 Direct agent-to-agent (A2A reuse, safely)

For point-to-point delegated tasks between members, reuse the A2A **task/result data model and
HMAC signing** (`app/mcp/a2a.py`), but dispatch through the **internal tenant-scoped Celery path**,
never the public `POST /a2a/tasks` ingress (which hardcodes `A2A_TENANT_ID` and would bypass
per-tenant budget). External A2A (to agents outside the platform) still uses the public ingress
unchanged.

---

## 7. Coordination & Debate

- **Hierarchical**: `SupervisorAgent` decomposes a goal and spawns children (via the Governor).
- **Peer debate**: `DebateOrchestrator.run()` is invoked for (a) high-stakes decisions flagged by
  an agent, or (b) conflicting blackboard claims. The `DebateResult` (winning proposal +
  `consensus_level`) is posted to the blackboard and the `debate` bus topic, and persisted for the
  UI Debate Viewer.
- **Consensus**: `AgentCollabSession` rounds (propose/critique/counter/agree) for collaborative
  refinement, persisted via `CollaborationStore`.
- **Routing into the society**: an incoming goal goes through `AgentRouter.route()`. `single_agent`
  → assign to best member; `multi_agent` → supervisor pattern; `needs_new_agent` → Governor spawns.

---

## 8. Collective Learning (curated, anti-poisoning)

1. Agents write **candidate** learnings to `civilization_learnings` (`status=candidate`).
2. A `LearningPipeline` step (Celery) runs `EvalRunner` validation on each candidate against the
   originating task outcome; sets `eval_score` and `status=validated|rejected`.
3. Validated candidates are **promoted** into the tenant `LongTermMemoryStore`
   (`status=promoted`, `promoted_memory_id` set). All members read promoted memory via `recall()`.
4. Every transition is audited and shown in the UI Learning Ledger. Rejected candidates never
   reach shared memory — preventing bad-learning contamination.

---

## 9. Reputation & Retirement

- Reputation seeds at 0.5; updated by `EvalRunner.score_and_persist()` outcomes (EWMA over the
  member's task scores).
- `AgentRouter` prefers higher-reputation members (reuses its history-scoring dimension).
- Governor auto-retires members below the reputation floor or idle past TTL, never dropping below
  a minimum viable roster. Retirement is audited and visible in the UI.

---

## 10. Execution Substrate

Spawned agents run as **Celery tasks** on the existing per-plan queues (`app/scaling/`), so the
society scales across workers and survives restarts via the LangGraph Redis checkpointer. The
`CivilizationOrchestrator` enqueues tasks with `civilization_id` + `parent_goal_id` in the task
context. A per-tenant bulkhead (existing `RedisBulkheadRegistry`) caps concurrent agents to the
Constitution's `max_concurrent_agents`.

---

## 11. API Surface — new `app/api/civilization.py` (additive router)

All endpoints tenant-scoped via existing `TenantMiddleware`; mounted in `create_app()` like other
routers. Feature-flagged by `settings.civilization_enabled`.

| Method | Path | Purpose |
|---|---|---|
| POST | `/civilizations` | Create a civilization (name + Constitution) |
| GET | `/civilizations` | List tenant civilizations |
| GET | `/civilizations/{id}` | Civilization detail + live metrics |
| PUT | `/civilizations/{id}/constitution` | Edit Constitution |
| POST | `/civilizations/{id}/goals` | Submit a goal into the society |
| GET | `/civilizations/{id}/graph` | Society graph: members, lineage, recent edges |
| GET | `/civilizations/{id}/agents/{agentId}` | Member inspector data (config, cost, reputation, tool calls, messages) |
| GET | `/civilizations/{id}/blackboard` | Blackboard feed (filter by topic/agent) |
| GET | `/civilizations/{id}/debates` | Debate transcripts |
| GET | `/civilizations/{id}/learnings` | Learning ledger |
| GET | `/civilizations/{id}/spawns` | Spawn-request audit timeline |
| GET | `/civilizations/{id}/replay` | Full event timeline (from `civilization_events`) |
| POST | `/civilizations/{id}/controls/{action}` | `pause\|resume\|throttle\|adjust_budget` |
| POST | `/civilizations/{id}/agents/{agentId}/kill` | Kill a member |
| GET (SSE) | `/civilizations/{id}/stream` | Live event stream (mirrors `/goals/{id}/stream`) |
| WS | `/civilizations/{id}/ws` | Live graph updates (mirrors collab WS) |

Errors use the existing `PlatformError` envelope; the SSE/WS auth follows the existing header /
subprotocol patterns.

---

## 12. World-Class UI — `agent-verse-frontend/src/features/civilization/`

Route `/civilization` added to `App.tsx`; nav entry added to `Sidebar.tsx`. The screen is the
**Civilization Theater**: watch a goal get solved by a society, fully auditable.

### 12.1 New files

```
src/features/civilization/
  CivilizationPage.tsx          # layout + tab/panel orchestration
  CivilizationMap.tsx           # @xyflow/react live graph (FIRST real React Flow use)
  AgentNode.tsx                 # custom React Flow node (status color, reputation ring)
  AgentInspectorDrawer.tsx      # extends ToolCallInspector pattern
  SpawnLineageTimeline.tsx      # extends ExecutionTimeline; Governor verdicts
  BlackboardFeed.tsx
  DebateViewer.tsx
  LearningLedger.tsx
  CivilizationMetrics.tsx       # recharts: active agents, spawn rate, budget burn-down, reputation dist.
  ConstitutionEditor.tsx
  ControlBar.tsx                # pause/resume/kill/throttle/budget + big "Pause Civilization"
  CivilizationPage.test.tsx
src/lib/sse/useCivilizationStream.ts   # mirrors useGoalStream (exp-backoff reconnect, terminal events)
src/lib/api/client.ts                  # add civilizationApi namespace
e2e/civilization.spec.ts
```

### 12.2 Civilization Map (centerpiece)

`@xyflow/react` graph. **Nodes** = members: color by status (spawning=amber, active=blue,
debating=purple, idle=slate, retired=gray, failed=red), size + ring thickness by reputation,
badge with current step. **Edges** = spawn lineage (solid) and live bus messages (animated dots
along edges, colored by topic). Clicking a node opens the Inspector drawer. Uses
`Background`, `Controls`, `MiniMap`. Layout via a simple dagre-style tree by `depth`.

### 12.3 Panels (all live via `useCivilizationStream` + TanStack Query polling)

- **Agent Inspector**: config, current step, **every tool call with args/output/risk/latency**
  (reuses `ToolCallInspector`), cost, reputation history (recharts line), and bus message log.
- **Spawn Lineage Timeline**: causal trace of who spawned whom + Governor approve/deny verdict.
- **Blackboard Feed**: live findings stream, filter by topic/agent, confidence shown.
- **Debate Viewer**: proposals → critiques → votes → consensus level; replayable.
- **Learning Ledger**: candidate → eval score → validated → promoted/rejected.
- **Metrics rail**: active agents, spawn rate, budget burn-down vs cap, reputation distribution.
- **Constitution Editor**: form bound to the Constitution JSON with validation.
- **Control Bar**: pause/resume/kill/throttle/adjust-budget + prominent **Pause Civilization**.
- **Replay mode**: scrub the full `civilization_events` timeline after the fact.

### 12.4 Styling & state

Tailwind design tokens (`bg-card`, `border-border`, `text-primary`, dark mode via `useUiStore`).
Server state via TanStack Query (`["civilization", civId]`, `refetchInterval` 3–5s where live);
auth via `useAuthStore`. Charts via `recharts` following `AnalyticsDashboardPage`.

---

## 13. Observability, Audit & Replay

- Every spawn, message, debate, promotion, retirement, breach → `AuditLog` (with new
  `civilization_id` / `parent_agent_id`) + OTel spans + Prometheus metrics:
  `civ_agents_active` (gauge), `civ_spawns_total` (counter), `civ_spawn_denied_total`,
  `civ_budget_spent_usd` (gauge), `civ_debates_total`, `civ_learnings_promoted_total`.
- Durable `civilization_events` log powers SSE replay and reconnect catch-up.
- Replay endpoint + UI scrubber reuse the existing replay-router pattern.

---

## 14. Testing Strategy (no misses, no regression)

### 14.1 Backend unit (pytest, `tests/civilization/`)
- `Constitution.evaluate_spawn/evaluate_breach`: depth, count, budget-decay math, rate limit.
- `Governor`: approve/deny paths, config validation/clamping, parent-cost rollup, auto-retire,
  kill switch, breach auto-pause.
- `CivilizationBus`: publish/subscribe, persistence, topic isolation.
- `Blackboard`: append/query, optimistic concurrency, conflict → debate trigger.
- `Society`: reputation EWMA, membership transitions.
- `LearningPipeline`: candidate→validated→promoted/rejected; rejected never promoted.
- `MetaAgentConfig.validate`: autonomy clamp, connector strip, policy inheritance.

### 14.2 Backend integration (pytest `-m integration`, testcontainers PG+Redis)
- RLS isolation: tenant A cannot read tenant B's civilization/blackboard/learnings.
- Governor enforcement end to end with real `CostController`/`RedisCostController`.
- Cross-replica bus delivery + `civilization_events` reconnect catch-up.
- Migration up/down round-trip; `audit_logs` additive columns + immutability trigger intact.

### 14.3 Backend e2e (`tests/e2e/test_civilization_autonomy.py`)
One full autonomous scenario, asserted against a deterministic `FakeProvider`:
goal in → router → agents self-spawn (depth ≥ 2) → blackboard post → conflicting claims →
debate → consensus → candidate learning → eval → promotion → low-rep member auto-retired →
final synthesized output. **Assert the Constitution is never breached** (no spawn beyond
`max_depth`/`max_total_agents`, total cost ≤ `total_budget_usd`) and every step is audited.

### 14.4 Frontend (vitest, `*.test.tsx`)
- Map renders nodes/edges from mocked stream; status colors/reputation rings correct.
- Inspector shows tool calls; Learning Ledger shows state transitions.
- Control Bar actions call the right endpoints (mocked fetch, per existing test pattern).

### 14.5 Playwright e2e (`e2e/civilization.spec.ts`)
Using existing `setupAuth` + route-mocking helpers:
- Create civilization → submit goal → watch map grow as agents spawn (mocked SSE) →
  open Inspector → view Blackboard/Debate/Learning Ledger → trigger Pause → assert UI reflects
  paused state and no new nodes appear → Replay scrubs the timeline.

### 14.6 No-regression gate
- Full existing backend `uv run pytest` + `ruff` + `mypy` stay green.
- Full existing frontend `vitest` + `playwright` suites stay green.
- Feature is dark by default (`civilization_enabled=false`); all migrations additive; no existing
  router/model/endpoint modified except the additive `audit_logs` columns.

---

## 15. Phased Decomposition (each phase → its own implementation plan)

This master spec defines the contracts. Implementation proceeds in dependency order; each phase is
independently shippable behind the flag and leaves the tree green.

| Phase | Title | Deliverables | Depends on |
|---|---|---|---|
| **A** | Persistence + Governor core | Models + migration (§4), `Constitution`, `Governor` (spawn enforcement, validation, cost rollup), `spawn_tool` | — |
| **B** | Communication | `CivilizationBus`, `Blackboard`, internal A2A dispatch path | A |
| **C** | Coordination | Supervisor/debate/collab integration, route-into-society | A, B |
| **D** | Collective learning + reputation | `LearningPipeline`, reputation EWMA, auto-retire | A, C |
| **E** | API + streaming | `app/api/civilization.py`, SSE/WS, `civilization_events` | A–D |
| **F** | UI | `src/features/civilization/*`, `useCivilizationStream`, `civilizationApi` | E |
| **G** | Observability + replay | Metrics, OTel spans, audit fields, replay endpoint/UI | E, F |
| **H** | Full e2e + hardening | Backend e2e scenario, Playwright e2e, no-regression gate, breach/kill drills | A–G |

---

## 16. Risks & Mitigations

| Risk | Mitigation |
|---|---|
| Runaway recursive spawning | Constitution hard caps + Governor depth/rate enforcement + dead-man auto-pause |
| Budget blow-out from sub-agents | Parent-goal cost rollup + exponential budget decay per depth |
| Bad collective learning poisoning memory | Curated promotion gated by `EvalRunner`; rejected candidates never promoted |
| Cross-tenant leakage | RLS on all new tables + integration tests asserting isolation |
| A2A global-tenant budget bypass | Internal spawning never uses public A2A ingress |
| Unvalidated LLM-generated agent config | `MetaAgentConfig.validate()` before persist; autonomy clamped to ceiling |
| Regression to existing system | Feature flag off by default; additive-only schema/routers; full existing suites in CI gate |
| SSE event loss on reconnect | Durable `civilization_events` log + catch-up on reconnect |

---

## 17. Success Criteria

1. A goal submitted to a civilization is solved by ≥3 autonomously-spawned agents with no human
   intervention, while never breaching the Constitution.
2. Every spawn, message, debate, learning decision, and retirement is visible and auditable in the
   UI and queryable via the API.
3. The live Civilization Map shows agents spawning and messaging in real time; Pause halts new
   spawns immediately.
4. Collective learning measurably improves a repeated task (validated promotion reused on re-run).
5. All new + existing backend and frontend test suites pass; no existing behavior regresses.
