# Autonomous Org Brain (AOB) — Design

**Status:** Implemented (v1) — verified e2e 2026-09-14 (subagent-driven-development; safe defaults: autonomy_level=1, flag `org_autonomy_enabled` off, env kill switch `AV_ORG_AUTONOMY_DISABLED`)
**Date:** 2026-09-14
**Scope:** `agent-verse-backend/app/org/*`, `app/scaling/tasks.py` (`org_brain_loop`),
one Alembic migration, and `agent-verse-frontend/src/features/org/*` surfacing.

---

## 1. Problem & goal

Today the AI organization is a **tool you operate**: work only happens when a human
creates a mission or a cron schedule fires one. Agents collaborate only *inside* a
running mission. The org has an `autonomy_level` (default **1** = "propose only,
never execute") and a stubbed `org_brain_loop` that detects backlog and logs
`work_triggered` but never actually launches anything.

**Goal:** make the org a **self-running operations layer the user supervises** —
it pursues the org's charter (`mission`/`goals`) on its own and self-heals when
work stalls, *within budget and policy the operator sets*, with a full audit trail
of why it acted (or held back). Autonomy is a single dial (L1→L5) from advisor to
fully autonomous, and everything routes through **one guardrail chokepoint**.

### Locked decisions (from brainstorming)
- **Trigger:** pursue goals when idle **and** step in on trouble (blocked/failed/SLA).
- **Approval model:** **level-driven** (L3 propose→approve, L4 auto within policy, L5 full auto).
- **Collaboration:** **both** in-mission teamwork *and* ambient cross-mission chatter.
- **Approach:** **A (beat-driven "brain tick") + a bounded LLM planner** inside DECIDE
  (reuse `goal_refinement`), with deterministic guardrails around the intelligent core.

### Non-goals (v1)
- Sub-minute, event-driven reactions (v2 — see §12).
- Inventing goals beyond the org charter.
- Removing human approval for high-risk actions (external send / destructive / spend
  always gate, regardless of level).

---

## 2. Reuse map (what already exists)

| Need | Existing module |
|---|---|
| Charter to plan against | `Organization.mission/vision/goals/policies/risk_tolerance/monthly_budget_usd/settings` |
| Autonomy levels + per-level gates | `app/org/autonomy.py` (`AutonomyLevel`, `AutonomyEnforcer`) |
| Goal → mission proposal (budget-constrained) | `app/org/goal_refinement.py` (caps est. cost at ~10% monthly) |
| Cost-runaway / loop detection | `app/org/loop_detector.py` (`check_cost_runaway`, loop patterns) |
| Mission create / execute | `OrgService.create_mission`, `create_mission_and_execute`, `get_org_health` |
| Beat driver | `app/scaling/tasks.py::org_brain_loop` (scheduled every 5 min in `celery_app.py`) |
| Team roles / leads | `app/org/team_formation.py`, `roles.py`, `role_taxonomy.py` |
| Cheap model calls | `app/org/model_gateway.py` |
| Approvals / HITL | `ApprovalCenter`, org approval chain |
| Surfacing | org event stream (SSE) + `ActivityFeed` |

The AOB is primarily **wiring + a guardrail gate + an audit trail + surfacing**, not new machinery.

---

## 3. Architecture

New service `OrgBrain` (`app/org/brain.py`) driven by `org_brain_loop`. Per **active**
org, one **tick**:

```
SENSE ──▶ DECIDE ──▶ GUARDRAIL GATE ──▶ ACT ──▶ NARRATE
(state)  (proposals)  (allow/propose/block)  (mission)  (events + audit row)
```

Per-org work is isolated in try/except (one org failing never stalls others). A
separate, lower-frequency, hard-capped **collaboration tick** produces ambient chatter.

### 3.1 SENSE
Gather: `OrgService.get_org_health` (blocked/failed/SLA counts), active-mission count,
today's autonomous spend + launch count + last-launch timestamp (Redis), the org's
`goals`/`mission`/`policies`, and `settings.autonomy`.

### 3.2 DECIDE → `list[BrainDecision]`
- **Reactive rule (deterministic):** `blocked > blocked_threshold` OR `failed >
  failed_threshold` OR SLA breach → a remediation mission ("unblock / investigate X").
- **Proactive (bounded LLM planner):** if idle (`active_missions ≤ idle_threshold`)
  AND there are open `goals` not currently in flight → call `goal_refinement` to
  propose the next goal→mission (already budget-constrained). Dedup against goals
  already being pursued.
- `BrainDecision`: `{ kind: reactive|proactive, rationale, target_goal, est_cost_usd,
  risk_level, signature }`.

### 3.3 GUARDRAIL GATE — see §4.

### 3.4 ACT (by verdict + level — §5)
- `ALLOW_PROPOSE` → `create_mission(status="proposed")` → appears in ApprovalCenter.
- `ALLOW_EXECUTE` → `create_mission_and_execute(...)`; increment Redis launch/day + set
  last-launch; the mission's own high-risk steps still gate via HITL.
- `BLOCK` → no mission; record reason.

### 3.5 NARRATE
Write an `org_brain_decisions` audit row for **every** decision (including blocked/
held-back ones) and emit an org event so the Activity feed/constellation show the
reasoning.

---

## 4. The Guardrail Gate (safety core — one chokepoint, fail-closed)

`GuardrailGate.evaluate(decision, org, counters) -> Verdict` where
`Verdict ∈ {ALLOW_EXECUTE, ALLOW_PROPOSE, BLOCK(reason)}`. Checks run in order; the
first failure downgrades to *propose* or *block*:

1. **Kill switch** — `settings.autonomy.paused` OR env `AV_ORG_AUTONOMY_DISABLED` → BLOCK.
2. **Autonomy level** — origination requires **L3+** (`AutonomyEnforcer`). Below L3 →
   record proposal in the log only (ACT does nothing).
3. **Cooldown** — `now - last_launch ≥ min_interval_seconds` (default 600) else defer (BLOCK: cooldown).
4. **Daily launch cap** — `missions_today < max_missions_per_day` (default 8).
5. **Concurrency cap** — `active_autonomous < max_concurrent` (default 2).
6. **Duplicate/loop guard** — decision `signature` not equal to an active/recent
   autonomous mission (dedup); reuses `loop_detector` patterns.
7. **Cost** — `est_cost ≤ min(per_mission_ceiling, remaining_daily_budget)`, where
   `daily_budget = daily_budget_usd or monthly_budget_usd/30` and
   `per_mission_ceiling = per_mission_cost_ceiling_usd or monthly_budget_usd*0.10`.
   Also `loop_detector.check_cost_runaway(day_spend, daily_budget)`.
   **If the Redis counter is unavailable → fail-closed = ALLOW_PROPOSE at most** (never
   auto-execute blind).
8. **Risk gate** — if `risk_level` high OR the mission implies external-send/destructive/
   spend → force `ALLOW_PROPOSE` (human approval) regardless of level
   (`AutonomyEnforcer.requires_approval` / `check_external_send_permitted`).

Downgrade semantics: a decision that would `ALLOW_EXECUTE` but trips #7 (soft) or #8
becomes `ALLOW_PROPOSE`; tripping #1/#3/#4/#5/#6 becomes `BLOCK` with a reason.

---

## 5. Autonomy-level behavior (the single dial)

| Level | Behavior |
|---|---|
| **L1** (default) | SENSE + log *proposals only*. No missions, no chatter. **Zero change from today.** |
| **L2** | Same as L1, plus surfaces suggestions in the UI. No execution. |
| **L3** | Creates missions **proposed** → ApprovalCenter; operator approves to run. Collaboration on (capped). |
| **L4** | Auto-creates **and executes** within policy/caps; high-risk steps still HITL. |
| **L5** | Fully autonomous within caps; broadest permitted action set; still bound by all §4 caps + kill switch. |

The level controls only **propose-vs-execute** and permitted action breadth; the hard
caps and kill switch bind every level.

---

## 6. Ambient inter-agent collaboration ("the team talks")

Separate **collaboration tick** (default every 15 min, **off by default**, L3+ only):
- A few department leads (`team_formation`/`roles`) exchange **short** status/next-step
  messages via a cheap model (`model_gateway`), capped at `collab_messages_per_tick`
  and a small per-message token budget.
- Persisted as `org.collaboration.message` events → Activity feed + a new **Team
  channel** panel; signals feed the next DECIDE (e.g., a lead flags a risk → reactive).
- **Cost control:** dedicated `collaboration_daily_budget_usd`; the tick is **skipped**
  once the org's daily budget is mostly spent. Most cost-sensitive piece → most tightly
  capped and one-toggle-off.

---

## 7. Data & state

- **`Organization.settings.autonomy`** (JSONB — no schema migration):
  `{ paused, cadence_seconds, max_concurrent, max_missions_per_day, min_interval_seconds,
  daily_budget_usd (0=derive), per_mission_cost_ceiling_usd (0=derive),
  blocked_threshold, failed_threshold, idle_threshold, collaboration_enabled,
  collaboration_daily_budget_usd, collab_messages_per_tick }`. Sensible defaults applied
  when keys are absent.
- **Redis** (per org, per UTC day, ~48 h TTL): `autonomous_spend_usd`,
  `autonomous_missions_count`, `last_launch_ts`; plus a short **per-org tick lock**
  (SET NX) so overlapping ticks can't double-launch.
- **New table `org_brain_decisions`** (audit / "why did it do that"):
  `id, org_id, tenant_id, tick_id, kind, rationale, target_goal, action(none|proposed|
  executed|blocked), guardrail_verdict, reason, est_cost_usd, mission_id?, created_at`.
  One Alembic migration; RLS-scoped by tenant like other org tables.

---

## 8. Surfacing (frontend)

- **Autonomy control** on the org page (drawer/panel): level selector L1–L5, a
  prominent **Pause autonomy** toggle, caps (daily budget, max concurrent, max/day,
  cadence), collaboration toggle + budget → `PATCH` org settings.
- **Brain feed**: timeline of `org_brain_decisions` — what it did *and what it held
  back and why* (trust surface).
- **ApprovalCenter**: L3 proposed autonomous missions for approve/reject.
- **Activity feed / Team channel**: brain narration + ambient chatter.

---

## 9. Failure modes & safety

Fail-closed throughout: errors in SENSE/DECIDE/GUARD skip acting that tick (logged);
planner error = "no proposal"; Redis down = propose-only; tick lock + cooldown + dedup
prevent double-launch; `loop_detector` + hard daily caps + kill switch cap runaway.
Feature-flagged per tenant/org via existing `feature_flags`.

---

## 10. Testing

- **Unit:** Guardrail Gate truth-matrix (each guard blocks/allows correctly; fail-closed
  on missing counters; soft-cost/risk downgrade to propose); DECIDE rules (reactive
  thresholds, proactive idle+goal, dedup); level mapping (L1 no-op, L3 propose, L4
  execute).
- **Integration (FakeProvider):** full tick → proposes at L3, executes at L4, respects
  caps, writes decision rows; safety tests (budget exceeded → block, at concurrency cap
  → defer, paused → no-op, duplicate → skip); collaboration tick respects its budget/off
  switch.

---

## 11. Rollout / safe defaults

Global default stays **L1** → nothing changes until an operator raises an org.
Conservative defaults: `max_concurrent=2`, `max_missions_per_day=8`, `cadence=300s`,
`min_interval=600s`, `daily_budget=monthly/30`, `per_mission ≤10% monthly`,
`collaboration off`. Global env kill switch `AV_ORG_AUTONOMY_DISABLED`. Behind a
feature flag.

---

## 12. Phased implementation outline

- **Phase 1 — Guardrail Gate + state:** `org_brain_decisions` migration, Redis counters,
  `GuardrailGate` with full unit truth-matrix. (Safety first, no behavior change.)
- **Phase 2 — Brain tick (reactive + proactive):** `OrgBrain` SENSE/DECIDE/ACT/NARRATE
  wired into `org_brain_loop`; reactive rule + bounded `goal_refinement` planner;
  level-driven propose/execute. Integration tests.
- **Phase 3 — Ambient collaboration tick:** capped lead chatter → events; feeds DECIDE.
- **Phase 4 — Frontend:** autonomy control panel, brain-decisions feed, Team channel,
  ApprovalCenter wiring.

## 13. Later (v2)
Real-time event-driven reactions (sub-minute), multi-goal prioritization/dependencies,
`self_improvement` tuning thresholds from outcomes.

---

## 14. Open questions
None blocking. Tunables (thresholds, default caps) ship as org settings and can be
adjusted without code changes.
