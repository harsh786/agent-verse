# Autonomous Org Brain Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the AI org self-run within operator-set budget/policy — a beat-driven brain that pursues the org charter and self-heals, gated by one deterministic guardrail chokepoint.

**Architecture:** A new `OrgBrain` service runs a SENSE→DECIDE→GUARD→ACT tick per active org on the existing `org_brain_loop` beat task. DECIDE uses a deterministic reactive rule plus a bounded LLM planner (reusing `goal_refinement`). Every autonomous action passes a single `GuardrailGate` (kill switch, autonomy level, cooldown, daily/concurrency caps, dedup, cost budget fail-closed, risk gate) before ACT creates a `proposed` (L3) or executed (L4/L5) mission. A capped collaboration tick produces ambient chatter. All decisions are written to an `org_brain_decisions` audit table and surfaced in the org UI.

**Tech Stack:** Python 3.12 · FastAPI · SQLAlchemy 2 async + asyncpg · Alembic · Celery + celery beat · Redis (async) · pytest (`uv run pytest`, `filterwarnings=error`) · React 19 + TypeScript + Vite + TanStack Query + vitest.

**Spec:** `docs/superpowers/specs/2026-09-14-autonomous-org-brain-design.md`

## Global Constraints

- **Backend commands run from `agent-verse-backend/` with `uv run`** (system Python is 3.9; the project pins 3.12). e.g. `uv run pytest`, `uv run alembic ...`, `uv run mypy app`.
- **Tests treat warnings as errors** (`filterwarnings = ["error"]`); do not introduce un-scoped deprecations.
- **Lint/type:** ruff line-length 100, target py312, rules `E,F,I,N,UP,B,A,C4,SIM,RUF`; mypy `strict` with the pydantic plugin. Run `uv run ruff check .` and `uv run mypy app` before each commit.
- **Multi-tenancy:** all new tables carry `tenant_id` and are accessed under RLS (`app.tenancy`/`app.db.rls`); org data is tenant-scoped.
- **Safe by default:** org `autonomy_level` default stays **1**; the brain must be a no-op for L1/L2 orgs and when paused. Global kill switch env var: `AV_ORG_AUTONOMY_DISABLED`. Feature flag: `org_autonomy_enabled` (via `app/org/feature_flags.py::is_feature_enabled`).
- **Default caps (applied when a settings key is absent):** `cadence_seconds=300`, `min_interval_seconds=600`, `max_concurrent=2`, `max_missions_per_day=8`, `daily_budget_usd=0` (0 ⇒ derive `monthly_budget_usd/30`), `per_mission_cost_ceiling_usd=0` (0 ⇒ derive `monthly_budget_usd*0.10`), `blocked_threshold=5`, `failed_threshold=2`, `idle_threshold=1`, `collaboration_enabled=false`, `collaboration_daily_budget_usd=1.0`, `collab_messages_per_tick=4`.
- **Frontend commands run from `agent-verse-frontend/`:** `npm run typecheck`, `npm run test`, `npm run build`.
- **Reuse, do not rebuild:** `AutonomyEnforcer` (`app/org/autonomy.py`), `goal_refinement` (`app/org/goal_refinement.py`), `LoopDetector.check_cost_runaway` (`app/org/loop_detector.py`), `OrgService.create_mission / create_mission_and_execute / get_org_health` (`app/org/service.py`).

**Verified existing signatures (consume as-is):**
- `AutonomyEnforcer.get_config(level:int) -> AutonomyLevelConfig`; `.requires_approval(action:str, effective_level:int) -> bool`; `.check_external_send_permitted(effective_level:int) -> bool`; `.check_write_permitted(effective_level:int) -> bool`.
- `LoopDetector.check_cost_runaway(spent_usd:float, budget_usd:float) -> LoopDetection` where `LoopDetection` has `.detected:bool, .pattern:str, .details:str, .recommended_action:str`.
- `OrgService.get_org_health(org_id:str) -> dict` with `["task_counts"]` (keys include `"blocked"`, `"failed"`), `["items_needing_attention"]`.
- `OrgService.create_mission(*, org_id, title, objective="", why="", expected_outcome="", priority="medium", source="manual", budget_usd=None, autonomy_level=None, created_by=None, ...) -> OrgMission` (OrgMission has `.id`, `.status`).
- `OrgService.create_mission_and_execute(*, org_id, title, objective="", ..., source="api", app_state=None, tenant_ctx=None) -> tuple[OrgMission, dict]`.
- `is_feature_enabled(flag:str, tenant_id:str|None=None) -> bool`.
- `OrgMission.status` default `"active"`; org autonomy stored on `Organization.settings` (JSONB) + `Organization.autonomy_level` (int, default 1), `Organization.monthly_budget_usd` (float), `Organization.goals` (JSONB list), `Organization.mission` (text).

---

# Phase 1 — Guardrail Gate + state (safety first, no behavior change)

### Task 1: Autonomy settings resolver

Read-through of `Organization.settings["autonomy"]` merged over the default caps, so every later task reads one typed object.

**Files:**
- Create: `agent-verse-backend/app/org/brain_settings.py`
- Test: `agent-verse-backend/tests/org/test_brain_settings.py`

**Interfaces:**
- Produces: `AutonomySettings` (frozen dataclass) with fields: `paused:bool, cadence_seconds:int, min_interval_seconds:int, max_concurrent:int, max_missions_per_day:int, daily_budget_usd:float, per_mission_cost_ceiling_usd:float, blocked_threshold:int, failed_threshold:int, idle_threshold:int, collaboration_enabled:bool, collaboration_daily_budget_usd:float, collab_messages_per_tick:int`; and `resolve_autonomy_settings(settings:dict|None, monthly_budget_usd:float) -> AutonomySettings` (applies defaults; derives `daily_budget_usd = monthly_budget_usd/30` and `per_mission_cost_ceiling_usd = monthly_budget_usd*0.10` when the stored value is 0).

- [ ] **Step 1: Write the failing test**

```python
# tests/org/test_brain_settings.py
from app.org.brain_settings import resolve_autonomy_settings


def test_defaults_applied_when_settings_empty():
    s = resolve_autonomy_settings(None, monthly_budget_usd=300.0)
    assert s.paused is False
    assert s.cadence_seconds == 300
    assert s.max_concurrent == 2
    assert s.max_missions_per_day == 8
    assert s.blocked_threshold == 5
    assert s.collaboration_enabled is False


def test_zero_budget_fields_derive_from_monthly():
    s = resolve_autonomy_settings({"autonomy": {}}, monthly_budget_usd=300.0)
    assert s.daily_budget_usd == 10.0            # 300 / 30
    assert s.per_mission_cost_ceiling_usd == 30.0  # 300 * 0.10


def test_explicit_values_override_defaults_and_derivation():
    raw = {"autonomy": {"paused": True, "max_concurrent": 5, "daily_budget_usd": 4.0}}
    s = resolve_autonomy_settings(raw, monthly_budget_usd=300.0)
    assert s.paused is True
    assert s.max_concurrent == 5
    assert s.daily_budget_usd == 4.0  # explicit, not derived
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd agent-verse-backend && uv run pytest tests/org/test_brain_settings.py -q`
Expected: FAIL (`ModuleNotFoundError: app.org.brain_settings`).

- [ ] **Step 3: Write minimal implementation**

```python
# app/org/brain_settings.py
"""Typed, defaulted view of Organization.settings["autonomy"]."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

_DEFAULTS: dict[str, Any] = {
    "paused": False,
    "cadence_seconds": 300,
    "min_interval_seconds": 600,
    "max_concurrent": 2,
    "max_missions_per_day": 8,
    "daily_budget_usd": 0.0,
    "per_mission_cost_ceiling_usd": 0.0,
    "blocked_threshold": 5,
    "failed_threshold": 2,
    "idle_threshold": 1,
    "collaboration_enabled": False,
    "collaboration_daily_budget_usd": 1.0,
    "collab_messages_per_tick": 4,
}


@dataclass(frozen=True)
class AutonomySettings:
    paused: bool
    cadence_seconds: int
    min_interval_seconds: int
    max_concurrent: int
    max_missions_per_day: int
    daily_budget_usd: float
    per_mission_cost_ceiling_usd: float
    blocked_threshold: int
    failed_threshold: int
    idle_threshold: int
    collaboration_enabled: bool
    collaboration_daily_budget_usd: float
    collab_messages_per_tick: int


def resolve_autonomy_settings(
    settings: dict[str, Any] | None, monthly_budget_usd: float
) -> AutonomySettings:
    raw = dict(_DEFAULTS)
    if settings and isinstance(settings.get("autonomy"), dict):
        for k, v in settings["autonomy"].items():
            if k in raw and v is not None:
                raw[k] = v
    daily = float(raw["daily_budget_usd"]) or (float(monthly_budget_usd) / 30.0)
    ceiling = float(raw["per_mission_cost_ceiling_usd"]) or (float(monthly_budget_usd) * 0.10)
    return AutonomySettings(
        paused=bool(raw["paused"]),
        cadence_seconds=int(raw["cadence_seconds"]),
        min_interval_seconds=int(raw["min_interval_seconds"]),
        max_concurrent=int(raw["max_concurrent"]),
        max_missions_per_day=int(raw["max_missions_per_day"]),
        daily_budget_usd=daily,
        per_mission_cost_ceiling_usd=ceiling,
        blocked_threshold=int(raw["blocked_threshold"]),
        failed_threshold=int(raw["failed_threshold"]),
        idle_threshold=int(raw["idle_threshold"]),
        collaboration_enabled=bool(raw["collaboration_enabled"]),
        collaboration_daily_budget_usd=float(raw["collaboration_daily_budget_usd"]),
        collab_messages_per_tick=int(raw["collab_messages_per_tick"]),
    )
```

- [ ] **Step 4: Run test + lint/type to verify they pass**

Run: `uv run pytest tests/org/test_brain_settings.py -q && uv run ruff check app/org/brain_settings.py && uv run mypy app/org/brain_settings.py`
Expected: PASS, no lint/type errors.

- [ ] **Step 5: Commit**

```bash
git add app/org/brain_settings.py tests/org/test_brain_settings.py
git commit -m "feat(org-brain): typed autonomy settings resolver with derived budgets"
```

---

### Task 2: Brain decision types + Guardrail Gate (the safety core)

Pure, deterministic verdict function — the single chokepoint. No I/O; counters/state are passed in.

**Files:**
- Create: `agent-verse-backend/app/org/brain_types.py`
- Create: `agent-verse-backend/app/org/brain_guardrails.py`
- Test: `agent-verse-backend/tests/org/test_brain_guardrails.py`

**Interfaces:**
- Consumes: `AutonomySettings` (Task 1); `AutonomyEnforcer` (`app/org/autonomy.py`); `LoopDetector` (`app/org/loop_detector.py`).
- Produces:
  - `BrainDecision` (dataclass): `kind:str  # "reactive"|"proactive", rationale:str, target_goal:str, est_cost_usd:float, risk_level:str  # "low"|"medium"|"high", signature:str`.
  - `TickCounters` (dataclass): `day_spend_usd:float, missions_today:int, active_autonomous:int, seconds_since_last_launch:float, recent_signatures:frozenset[str], counters_available:bool`.
  - `Verdict` (dataclass): `action:str  # "execute"|"propose"|"block", reason:str`.
  - `evaluate_guardrails(decision:BrainDecision, *, autonomy_level:int, settings:AutonomySettings, counters:TickCounters, kill_switch:bool, enforcer:AutonomyEnforcer, loop_detector:LoopDetector) -> Verdict`.

- [ ] **Step 1: Write the failing test**

```python
# tests/org/test_brain_guardrails.py
from app.org.autonomy import AutonomyEnforcer
from app.org.brain_guardrails import TickCounters, Verdict, evaluate_guardrails
from app.org.brain_settings import resolve_autonomy_settings
from app.org.brain_types import BrainDecision
from app.org.loop_detector import LoopDetector


def _settings(**over):
    base = {"autonomy": {"daily_budget_usd": 100.0, "per_mission_cost_ceiling_usd": 50.0, **over}}
    return resolve_autonomy_settings(base, monthly_budget_usd=3000.0)


def _decision(**over):
    d = dict(kind="proactive", rationale="advance goal", target_goal="g1",
             est_cost_usd=5.0, risk_level="low", signature="sig-1")
    d.update(over)
    return BrainDecision(**d)


def _counters(**over):
    c = dict(day_spend_usd=0.0, missions_today=0, active_autonomous=0,
             seconds_since_last_launch=100000.0, recent_signatures=frozenset(),
             counters_available=True)
    c.update(over)
    return TickCounters(**c)


def _call(level, **kw):
    return evaluate_guardrails(
        _decision(**kw.pop("decision", {})),
        autonomy_level=level,
        settings=kw.pop("settings", _settings()),
        counters=kw.pop("counters", _counters()),
        kill_switch=kw.pop("kill_switch", False),
        enforcer=AutonomyEnforcer(),
        loop_detector=LoopDetector(),
    )


def test_kill_switch_blocks():
    assert _call(4, kill_switch=True).action == "block"


def test_below_l3_blocks():
    assert _call(2).action == "block"


def test_l3_proposes_low_risk():
    assert _call(3).action == "propose"


def test_l4_executes_low_risk():
    assert _call(4).action == "execute"


def test_cooldown_blocks():
    v = _call(4, counters=_counters(seconds_since_last_launch=10.0),
              settings=_settings(min_interval_seconds=600))
    assert v.action == "block" and "cooldown" in v.reason.lower()


def test_daily_launch_cap_blocks():
    v = _call(4, counters=_counters(missions_today=8), settings=_settings(max_missions_per_day=8))
    assert v.action == "block"


def test_concurrency_cap_blocks():
    v = _call(4, counters=_counters(active_autonomous=2), settings=_settings(max_concurrent=2))
    assert v.action == "block"


def test_duplicate_signature_blocks():
    v = _call(4, counters=_counters(recent_signatures=frozenset({"sig-1"})))
    assert v.action == "block" and "duplicate" in v.reason.lower()


def test_cost_over_ceiling_downgrades_to_propose():
    v = _call(4, decision={"est_cost_usd": 999.0})
    assert v.action == "propose" and "cost" in v.reason.lower()


def test_cost_over_remaining_daily_budget_downgrades():
    v = _call(4, decision={"est_cost_usd": 40.0},
              counters=_counters(day_spend_usd=95.0), settings=_settings(daily_budget_usd=100.0))
    assert v.action == "propose"


def test_high_risk_forces_propose_even_at_l5():
    v = _call(5, decision={"risk_level": "high"})
    assert v.action == "propose" and "approval" in v.reason.lower()


def test_counters_unavailable_fails_closed_to_propose():
    v = _call(4, counters=_counters(counters_available=False))
    assert v.action == "propose"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/org/test_brain_guardrails.py -q`
Expected: FAIL (`ModuleNotFoundError: app.org.brain_types`).

- [ ] **Step 3: Write minimal implementation**

```python
# app/org/brain_types.py
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class BrainDecision:
    kind: str            # "reactive" | "proactive"
    rationale: str
    target_goal: str
    est_cost_usd: float
    risk_level: str      # "low" | "medium" | "high"
    signature: str       # stable dedup key for this intended work
```

```python
# app/org/brain_guardrails.py
"""The single deterministic chokepoint every autonomous action passes.

Ordered checks; the first failure downgrades to propose or block. Fail-closed:
missing counters or an over-budget/high-risk action never auto-executes.
"""
from __future__ import annotations

import os
from dataclasses import dataclass

from app.org.autonomy import AutonomyEnforcer
from app.org.brain_settings import AutonomySettings
from app.org.brain_types import BrainDecision
from app.org.loop_detector import LoopDetector

_MIN_AUTONOMY_LEVEL = 3


@dataclass(frozen=True)
class TickCounters:
    day_spend_usd: float
    missions_today: int
    active_autonomous: int
    seconds_since_last_launch: float
    recent_signatures: frozenset[str]
    counters_available: bool


@dataclass(frozen=True)
class Verdict:
    action: str  # "execute" | "propose" | "block"
    reason: str


def _global_kill_switch() -> bool:
    return os.getenv("AV_ORG_AUTONOMY_DISABLED", "").lower() in {"1", "true", "yes"}


def evaluate_guardrails(
    decision: BrainDecision,
    *,
    autonomy_level: int,
    settings: AutonomySettings,
    counters: TickCounters,
    kill_switch: bool,
    enforcer: AutonomyEnforcer,
    loop_detector: LoopDetector,
) -> Verdict:
    # 1. Kill switch (per-org paused or global env)
    if kill_switch or settings.paused or _global_kill_switch():
        return Verdict("block", "autonomy paused (kill switch)")
    # 2. Autonomy level — origination requires L3+
    if autonomy_level < _MIN_AUTONOMY_LEVEL:
        return Verdict("block", f"autonomy level {autonomy_level} below L3")
    # 3. Cooldown
    if counters.seconds_since_last_launch < settings.min_interval_seconds:
        return Verdict("block", "cooldown between autonomous launches not elapsed")
    # 4. Daily launch cap
    if counters.missions_today >= settings.max_missions_per_day:
        return Verdict("block", "daily autonomous mission cap reached")
    # 5. Concurrency cap
    if counters.active_autonomous >= settings.max_concurrent:
        return Verdict("block", "concurrent autonomous mission cap reached")
    # 6. Duplicate / loop guard
    if decision.signature in counters.recent_signatures:
        return Verdict("block", "duplicate of a recent/active autonomous mission")
    # Baseline verdict from level: L3 proposes, L4+ executes.
    baseline = "propose" if autonomy_level == _MIN_AUTONOMY_LEVEL else "execute"
    # 7. Cost — fail closed when counters unavailable or over budget/ceiling.
    if not counters.counters_available:
        return Verdict("propose", "spend counters unavailable — proposing (fail-closed)")
    remaining = settings.daily_budget_usd - counters.day_spend_usd
    if decision.est_cost_usd > settings.per_mission_cost_ceiling_usd:
        return Verdict("propose", "estimated cost over per-mission ceiling")
    if decision.est_cost_usd > max(0.0, remaining):
        return Verdict("propose", "estimated cost over remaining daily budget")
    runaway = loop_detector.check_cost_runaway(counters.day_spend_usd, settings.daily_budget_usd)
    if runaway.detected:
        return Verdict("block", f"cost runaway: {runaway.details}")
    # 8. Risk gate — high-risk / external-send always needs approval.
    if decision.risk_level == "high" or not enforcer.check_external_send_permitted(autonomy_level):
        if decision.risk_level == "high":
            return Verdict("propose", "high-risk action requires human approval")
    return Verdict(baseline, "within policy")
```

- [ ] **Step 4: Run tests + lint/type**

Run: `uv run pytest tests/org/test_brain_guardrails.py -q && uv run ruff check app/org/brain_guardrails.py app/org/brain_types.py && uv run mypy app/org/brain_guardrails.py app/org/brain_types.py`
Expected: PASS, clean. (If `check_external_send_permitted` returns True at L4/L5, low-risk still executes; adjust only if a test fails.)

- [ ] **Step 5: Commit**

```bash
git add app/org/brain_types.py app/org/brain_guardrails.py tests/org/test_brain_guardrails.py
git commit -m "feat(org-brain): deterministic guardrail gate (kill switch, caps, cost, risk)"
```

---

### Task 3: `org_brain_decisions` table + store (audit trail)

**Files:**
- Create: `agent-verse-backend/app/db/migrations/versions/0209_org_brain_decisions.py` (use the next free revision number — check `uv run alembic heads`)
- Modify: `agent-verse-backend/app/org/models.py` (add `OrgBrainDecision`)
- Create: `agent-verse-backend/app/org/brain_store.py`
- Test: `agent-verse-backend/tests/org/test_brain_store.py` (marked `integration` — needs Postgres via testcontainers)

**Interfaces:**
- Produces: `OrgBrainDecision` model (columns: `id UUID pk`, `org_id UUID`, `tenant_id UUID`, `tick_id str`, `kind str`, `rationale text`, `target_goal str`, `action str`, `guardrail_verdict str`, `reason text`, `est_cost_usd float`, `mission_id UUID null`, `created_at tz`); `BrainDecisionStore(session)` with `async record(*, org_id, tenant_id, tick_id, kind, rationale, target_goal, action, guardrail_verdict, reason, est_cost_usd, mission_id=None) -> str` and `async list(org_id, tenant_id, limit=50) -> list[dict]`.

- [ ] **Step 1: Check current migration head**

Run: `cd agent-verse-backend && uv run alembic heads`
Note the head revision; set `down_revision` to it in the new migration. Rename the file's revision prefix to be > the current max.

- [ ] **Step 2: Write the failing test**

```python
# tests/org/test_brain_store.py
import pytest

pytestmark = pytest.mark.integration


@pytest.mark.asyncio
async def test_record_and_list_roundtrip(org_db_session, seeded_org):
    from app.org.brain_store import BrainDecisionStore

    store = BrainDecisionStore(org_db_session)
    did = await store.record(
        org_id=seeded_org.id, tenant_id=seeded_org.tenant_id, tick_id="tick-1",
        kind="proactive", rationale="advance goal g1", target_goal="g1",
        action="proposed", guardrail_verdict="propose", reason="within policy",
        est_cost_usd=5.0, mission_id=None,
    )
    assert did
    rows = await store.list(seeded_org.id, seeded_org.tenant_id, limit=10)
    assert rows and rows[0]["kind"] == "proactive"
    assert rows[0]["action"] == "proposed"
```

(If fixtures `org_db_session`/`seeded_org` don't exist, mirror an existing integration test under `tests/org/` — reuse its fixtures/conftest; do not invent new infra.)

- [ ] **Step 3: Run test to verify it fails**

Run: `DOCKER_HOST="unix:///Users/harsh/.colima/default/docker.sock" TESTCONTAINERS_RYUK_DISABLED=true uv run pytest tests/org/test_brain_store.py -q -m integration`
Expected: FAIL (no `brain_store` / no table). (Requires `colima start` + `docker-compose -f infra/docker-compose.yml up -d postgres redis`.)

- [ ] **Step 4: Add the model**

```python
# app/org/models.py  (append near the other org models; match existing imports/patterns)
class OrgBrainDecision(Base):
    __tablename__ = "org_brain_decisions"
    __table_args__ = (Index("idx_obd_org_created", "org_id", "created_at"),)

    id = Column(PG_UUID(as_uuid=True), primary_key=True, default=_uuid7)
    org_id = Column(PG_UUID(as_uuid=True), nullable=False)
    tenant_id = Column(PG_UUID(as_uuid=True), nullable=False)
    tick_id = Column(String(64), nullable=False)
    kind = Column(String(32), nullable=False)
    rationale = Column(Text, default="")
    target_goal = Column(String(200), default="")
    action = Column(String(32), nullable=False)            # none|proposed|executed|blocked
    guardrail_verdict = Column(String(32), nullable=False)  # execute|propose|block
    reason = Column(Text, default="")
    est_cost_usd = Column(Float, default=0.0)
    mission_id = Column(PG_UUID(as_uuid=True), nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
```

- [ ] **Step 5: Write the migration**

```python
# app/db/migrations/versions/0209_org_brain_decisions.py
"""org_brain_decisions audit table"""
from alembic import op

revision = "0209_org_brain_decisions"
down_revision = "<PASTE CURRENT HEAD FROM STEP 1>"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS org_brain_decisions (
            id                UUID PRIMARY KEY,
            org_id            UUID NOT NULL,
            tenant_id         UUID NOT NULL,
            tick_id           VARCHAR(64) NOT NULL,
            kind              VARCHAR(32) NOT NULL,
            rationale         TEXT DEFAULT '',
            target_goal       VARCHAR(200) DEFAULT '',
            action            VARCHAR(32) NOT NULL,
            guardrail_verdict VARCHAR(32) NOT NULL,
            reason            TEXT DEFAULT '',
            est_cost_usd      DOUBLE PRECISION DEFAULT 0,
            mission_id        UUID,
            created_at        TIMESTAMPTZ NOT NULL DEFAULT now()
        );
        CREATE INDEX IF NOT EXISTS idx_obd_org_created ON org_brain_decisions (org_id, created_at);
        ALTER TABLE org_brain_decisions ENABLE ROW LEVEL SECURITY;
        CREATE POLICY org_brain_decisions_tenant ON org_brain_decisions
            USING (tenant_id = current_setting('app.tenant_id', true)::uuid);
        """
    )


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS org_brain_decisions CASCADE;")
```

(Match the RLS pattern of neighbouring org migrations exactly — copy their `current_setting('app.tenant_id', ...)` form if it differs.)

- [ ] **Step 6: Write the store**

```python
# app/org/brain_store.py
from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy import text as sa_text


class BrainDecisionStore:
    def __init__(self, session: Any) -> None:
        self._s = session

    async def record(self, *, org_id, tenant_id, tick_id, kind, rationale, target_goal,
                     action, guardrail_verdict, reason, est_cost_usd, mission_id=None) -> str:
        did = str(uuid.uuid4())
        await self._s.execute(
            sa_text(
                "INSERT INTO org_brain_decisions (id, org_id, tenant_id, tick_id, kind, "
                "rationale, target_goal, action, guardrail_verdict, reason, est_cost_usd, mission_id) "
                "VALUES (CAST(:id AS uuid), CAST(:org AS uuid), CAST(:ten AS uuid), :tick, :kind, "
                ":rat, :goal, :act, :verd, :reason, :cost, "
                "CASE WHEN :mid IS NULL THEN NULL ELSE CAST(:mid AS uuid) END)"
            ),
            {"id": did, "org": str(org_id), "ten": str(tenant_id), "tick": tick_id, "kind": kind,
             "rat": rationale, "goal": target_goal, "act": action, "verd": guardrail_verdict,
             "reason": reason, "cost": float(est_cost_usd),
             "mid": str(mission_id) if mission_id else None},
        )
        return did

    async def list(self, org_id, tenant_id, limit: int = 50) -> list[dict[str, Any]]:
        rows = (await self._s.execute(
            sa_text(
                "SELECT id, tick_id, kind, rationale, target_goal, action, guardrail_verdict, "
                "reason, est_cost_usd, mission_id, created_at FROM org_brain_decisions "
                "WHERE org_id = CAST(:org AS uuid) ORDER BY created_at DESC LIMIT :lim"
            ),
            {"org": str(org_id), "lim": limit},
        )).mappings().all()
        return [dict(r) for r in rows]
```

- [ ] **Step 7: Run migration + tests + lint/type**

Run: `uv run alembic upgrade head && DOCKER_HOST=... TESTCONTAINERS_RYUK_DISABLED=true uv run pytest tests/org/test_brain_store.py -q -m integration && uv run ruff check app/org/brain_store.py && uv run mypy app/org/brain_store.py`
Expected: PASS.

- [ ] **Step 8: Commit**

```bash
git add app/org/models.py app/org/brain_store.py app/db/migrations/versions/0209_org_brain_decisions.py tests/org/test_brain_store.py
git commit -m "feat(org-brain): org_brain_decisions audit table + store (RLS-scoped)"
```

---

### Task 4: Redis tick counters + tick lock

**Files:**
- Create: `agent-verse-backend/app/org/brain_counters.py`
- Test: `agent-verse-backend/tests/org/test_brain_counters.py` (use `fakeredis.aioredis` if available in dev deps; else mark `integration` and use a real Redis)

**Interfaces:**
- Produces: `BrainCounters(redis, org_id)` with `async snapshot() -> tuple[float,int,float]  # (day_spend_usd, missions_today, seconds_since_last_launch)`; `async record_launch(est_cost_usd:float) -> None` (increments day spend + count, sets last-launch ts, all with ~48h TTL); `async acquire_tick_lock(ttl_seconds:int=120) -> bool` (SET NX). Keys namespaced `orgbrain:{org_id}:{utc_date}:...`.

- [ ] **Step 1: Write the failing test**

```python
# tests/org/test_brain_counters.py
import pytest


@pytest.mark.asyncio
async def test_snapshot_and_record_launch():
    try:
        import fakeredis.aioredis as fr
    except ImportError:
        pytest.skip("fakeredis not available")
    r = fr.FakeRedis()
    from app.org.brain_counters import BrainCounters

    c = BrainCounters(r, org_id="org-1")
    spend, count, since = await c.snapshot()
    assert spend == 0.0 and count == 0 and since >= 10**8  # never launched → huge gap

    await c.record_launch(est_cost_usd=3.5)
    spend, count, since = await c.snapshot()
    assert spend == 3.5 and count == 1 and since < 5


@pytest.mark.asyncio
async def test_tick_lock_is_exclusive():
    try:
        import fakeredis.aioredis as fr
    except ImportError:
        pytest.skip("fakeredis not available")
    r = fr.FakeRedis()
    from app.org.brain_counters import BrainCounters

    c = BrainCounters(r, org_id="org-2")
    assert await c.acquire_tick_lock() is True
    assert await c.acquire_tick_lock() is False  # already held
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/org/test_brain_counters.py -q`
Expected: FAIL (`ModuleNotFoundError`). (If fakeredis missing, tests skip — then add `fakeredis` to dev deps: `uv add --dev fakeredis` and re-run.)

- [ ] **Step 3: Write minimal implementation**

```python
# app/org/brain_counters.py
from __future__ import annotations

import time
from datetime import UTC, datetime
from typing import Any

_TTL = 48 * 3600


class BrainCounters:
    def __init__(self, redis: Any, org_id: str) -> None:
        self._r = redis
        self._org = org_id

    def _day(self) -> str:
        return datetime.now(UTC).strftime("%Y-%m-%d")

    def _k(self, suffix: str) -> str:
        return f"orgbrain:{self._org}:{self._day()}:{suffix}"

    async def snapshot(self) -> tuple[float, int, float]:
        spend = await self._r.get(self._k("spend"))
        count = await self._r.get(self._k("count"))
        last = await self._r.get(f"orgbrain:{self._org}:last_launch")
        since = (time.time() - float(last)) if last else 1e9
        return (float(spend or 0.0), int(count or 0), since)

    async def record_launch(self, est_cost_usd: float) -> None:
        await self._r.incrbyfloat(self._k("spend"), float(est_cost_usd))
        await self._r.expire(self._k("spend"), _TTL)
        await self._r.incr(self._k("count"))
        await self._r.expire(self._k("count"), _TTL)
        await self._r.set(f"orgbrain:{self._org}:last_launch", str(time.time()), ex=_TTL)

    async def acquire_tick_lock(self, ttl_seconds: int = 120) -> bool:
        got = await self._r.set(f"orgbrain:{self._org}:tick_lock", "1", nx=True, ex=ttl_seconds)
        return bool(got)
```

- [ ] **Step 4: Run tests + lint/type**

Run: `uv run pytest tests/org/test_brain_counters.py -q && uv run ruff check app/org/brain_counters.py && uv run mypy app/org/brain_counters.py`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add app/org/brain_counters.py tests/org/test_brain_counters.py
git commit -m "feat(org-brain): redis per-day spend/launch counters + exclusive tick lock"
```

---

# Phase 2 — Brain tick (reactive + proactive)

### Task 5: DECIDE — reactive rule + proactive planner adapter

Pure decision logic given a SENSE snapshot; the LLM planner is injected so it can be faked in tests.

**Files:**
- Create: `agent-verse-backend/app/org/brain_decide.py`
- Test: `agent-verse-backend/tests/org/test_brain_decide.py`

**Interfaces:**
- Consumes: `AutonomySettings` (Task 1), `BrainDecision` (Task 2).
- Produces: `OrgSnapshot` (dataclass): `blocked:int, failed:int, active_missions:int, open_goals:list[str], goals_in_flight:frozenset[str]`; `decide(snapshot:OrgSnapshot, settings:AutonomySettings, *, propose_goal_mission) -> list[BrainDecision]` where `propose_goal_mission: Callable[[str], tuple[str, float, str] | None]` returns `(rationale, est_cost_usd, risk_level)` for a goal or None. `signature` for reactive = `f"reactive:{reason}"`; for proactive = `f"proactive:{goal}"`.

- [ ] **Step 1: Write the failing test**

```python
# tests/org/test_brain_decide.py
from app.org.brain_decide import OrgSnapshot, decide
from app.org.brain_settings import resolve_autonomy_settings


def _s(**o):
    return resolve_autonomy_settings({"autonomy": o}, monthly_budget_usd=3000.0)


def _plan(goal):
    return (f"advance {goal}", 5.0, "low")


def test_reactive_when_blocked_over_threshold():
    snap = OrgSnapshot(blocked=6, failed=0, active_missions=3, open_goals=[], goals_in_flight=frozenset())
    out = decide(snap, _s(blocked_threshold=5), propose_goal_mission=_plan)
    assert any(d.kind == "reactive" for d in out)


def test_proactive_when_idle_with_open_goal():
    snap = OrgSnapshot(blocked=0, failed=0, active_missions=0, open_goals=["g1"], goals_in_flight=frozenset())
    out = decide(snap, _s(idle_threshold=1), propose_goal_mission=_plan)
    assert any(d.kind == "proactive" and d.target_goal == "g1" for d in out)


def test_no_proactive_when_goal_already_in_flight():
    snap = OrgSnapshot(blocked=0, failed=0, active_missions=0, open_goals=["g1"], goals_in_flight=frozenset({"g1"}))
    out = decide(snap, _s(idle_threshold=1), propose_goal_mission=_plan)
    assert all(d.target_goal != "g1" for d in out)


def test_no_proactive_when_busy():
    snap = OrgSnapshot(blocked=0, failed=0, active_missions=5, open_goals=["g1"], goals_in_flight=frozenset())
    out = decide(snap, _s(idle_threshold=1), propose_goal_mission=_plan)
    assert all(d.kind != "proactive" for d in out)


def test_planner_returning_none_yields_no_proactive():
    snap = OrgSnapshot(blocked=0, failed=0, active_missions=0, open_goals=["g1"], goals_in_flight=frozenset())
    out = decide(snap, _s(idle_threshold=1), propose_goal_mission=lambda g: None)
    assert all(d.kind != "proactive" for d in out)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/org/test_brain_decide.py -q` — Expected: FAIL (`ModuleNotFoundError`).

- [ ] **Step 3: Write minimal implementation**

```python
# app/org/brain_decide.py
from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from app.org.brain_settings import AutonomySettings
from app.org.brain_types import BrainDecision


@dataclass(frozen=True)
class OrgSnapshot:
    blocked: int
    failed: int
    active_missions: int
    open_goals: list[str]
    goals_in_flight: frozenset[str]


def decide(
    snapshot: OrgSnapshot,
    settings: AutonomySettings,
    *,
    propose_goal_mission: Callable[[str], tuple[str, float, str] | None],
) -> list[BrainDecision]:
    out: list[BrainDecision] = []
    # Reactive: step in on trouble.
    if snapshot.blocked > settings.blocked_threshold:
        out.append(BrainDecision("reactive", f"{snapshot.blocked} tasks blocked — remediate",
                                 "", 5.0, "low", "reactive:blocked"))
    if snapshot.failed > settings.failed_threshold:
        out.append(BrainDecision("reactive", f"{snapshot.failed} tasks failed — investigate",
                                 "", 5.0, "low", "reactive:failed"))
    # Proactive: pursue the charter when idle.
    if snapshot.active_missions <= settings.idle_threshold:
        for goal in snapshot.open_goals:
            if goal in snapshot.goals_in_flight:
                continue
            planned = propose_goal_mission(goal)
            if planned is None:
                continue
            rationale, est_cost, risk = planned
            out.append(BrainDecision("proactive", rationale, goal, float(est_cost), risk,
                                     f"proactive:{goal}"))
            break  # one proactive proposal per tick
    return out
```

- [ ] **Step 4: Run tests + lint/type** — `uv run pytest tests/org/test_brain_decide.py -q && uv run ruff check app/org/brain_decide.py && uv run mypy app/org/brain_decide.py` → PASS.

- [ ] **Step 5: Commit**

```bash
git add app/org/brain_decide.py tests/org/test_brain_decide.py
git commit -m "feat(org-brain): DECIDE — reactive thresholds + proactive goal planner adapter"
```

---

### Task 6: `OrgBrain` service — SENSE / ACT / NARRATE orchestration

Wires Tasks 1–5 + `OrgService` + `goal_refinement` into one `run_tick(org)` coroutine. Dependencies injected for testability.

**Files:**
- Create: `agent-verse-backend/app/org/brain.py`
- Test: `agent-verse-backend/tests/org/test_brain_service.py`

**Interfaces:**
- Consumes: `resolve_autonomy_settings`, `evaluate_guardrails`, `TickCounters`, `decide`, `OrgSnapshot`, `BrainCounters`, `BrainDecisionStore`, `OrgService`, `AutonomyEnforcer`, `LoopDetector`.
- Produces: `OrgBrain(org_service, brain_store, counters, enforcer, loop_detector, planner)` with `async run_tick(*, org_id, tenant_id, autonomy_level, org_settings, monthly_budget_usd, org_goals, org_mission) -> dict` returning `{"proposed":int,"executed":int,"blocked":int}`. `planner` is `Callable[[str, dict], tuple[str,float,str]|None]` (goal, org_context) → adapter over `goal_refinement`.
- ACT rules: verdict `execute` → `org_service.create_mission_and_execute(..., source="autonomous")` then `counters.record_launch(est_cost)`; verdict `propose` → `org_service.create_mission(..., source="autonomous", status="proposed")`; verdict `block` → no mission. Always `brain_store.record(...)`.

- [ ] **Step 1: Add `status` support to `create_mission`**

In `app/org/service.py::create_mission`, add parameter `status: str = "active"` and pass it into `OrgMission(status=status, ...)`. (Backward compatible — default unchanged.) Add a focused unit test `tests/org/test_create_mission_status.py` asserting a mission can be created with `status="proposed"`.

- [ ] **Step 2: Write the failing test (brain service, fully faked deps)**

```python
# tests/org/test_brain_service.py
import pytest

from app.org.brain import OrgBrain
from app.org.brain_guardrails import TickCounters


class _FakeMission:
    def __init__(self, mid="m1", status="active"):
        self.id, self.status = mid, status


class _FakeOrgService:
    def __init__(self, health):
        self._health = health
        self.created, self.executed = [], []

    async def get_org_health(self, org_id):
        return self._health

    async def create_mission(self, **kw):
        self.created.append(kw)
        return _FakeMission(status=kw.get("status", "active"))

    async def create_mission_and_execute(self, **kw):
        self.executed.append(kw)
        return _FakeMission(), {"dispatched": True}


class _FakeCounters:
    def __init__(self, **snap):
        self._snap = snap
        self.launches = 0

    async def snapshot(self):
        return (self._snap.get("spend", 0.0), self._snap.get("count", 0), self._snap.get("since", 1e9))

    async def record_launch(self, est_cost_usd):
        self.launches += 1


class _FakeStore:
    def __init__(self):
        self.rows = []

    async def record(self, **kw):
        self.rows.append(kw)
        return "d1"


def _brain(org_service, counters):
    from app.org.autonomy import AutonomyEnforcer
    from app.org.loop_detector import LoopDetector
    return OrgBrain(
        org_service=org_service, brain_store=_FakeStore(), counters=counters,
        enforcer=AutonomyEnforcer(), loop_detector=LoopDetector(),
        planner=lambda goal, ctx: (f"advance {goal}", 2.0, "low"),
    )


@pytest.mark.asyncio
async def test_l4_idle_org_executes_a_proactive_mission():
    svc = _FakeOrgService({"task_counts": {"blocked": 0, "failed": 0}})
    counters = _FakeCounters()
    brain = _brain(svc, counters)
    out = await brain.run_tick(org_id="o1", tenant_id="t1", autonomy_level=4,
                               org_settings={}, monthly_budget_usd=3000.0,
                               org_goals=["g1"], org_mission="grow")
    assert out["executed"] == 1 and svc.executed and counters.launches == 1


@pytest.mark.asyncio
async def test_l3_idle_org_only_proposes():
    svc = _FakeOrgService({"task_counts": {"blocked": 0, "failed": 0}})
    brain = _brain(svc, _FakeCounters())
    out = await brain.run_tick(org_id="o1", tenant_id="t1", autonomy_level=3,
                               org_settings={}, monthly_budget_usd=3000.0,
                               org_goals=["g1"], org_mission="grow")
    assert out["proposed"] == 1 and svc.created and svc.created[0]["status"] == "proposed"


@pytest.mark.asyncio
async def test_l1_org_is_noop():
    svc = _FakeOrgService({"task_counts": {"blocked": 9, "failed": 9}})
    brain = _brain(svc, _FakeCounters())
    out = await brain.run_tick(org_id="o1", tenant_id="t1", autonomy_level=1,
                               org_settings={}, monthly_budget_usd=3000.0,
                               org_goals=["g1"], org_mission="grow")
    assert out == {"proposed": 0, "executed": 0, "blocked": 1} or out["executed"] == 0
    assert not svc.created and not svc.executed
```

- [ ] **Step 3: Run test to verify it fails** — `uv run pytest tests/org/test_brain_service.py -q` → FAIL.

- [ ] **Step 4: Write minimal implementation**

```python
# app/org/brain.py
from __future__ import annotations

import uuid
from collections.abc import Callable
from typing import Any

from app.org.brain_decide import OrgSnapshot, decide
from app.org.brain_guardrails import TickCounters, evaluate_guardrails
from app.org.brain_settings import resolve_autonomy_settings
from app.observability.logging import get_logger

_log = get_logger(__name__)


class OrgBrain:
    def __init__(self, *, org_service, brain_store, counters, enforcer, loop_detector,
                 planner: Callable[[str, dict[str, Any]], tuple[str, float, str] | None]) -> None:
        self._svc = org_service
        self._store = brain_store
        self._counters = counters
        self._enforcer = enforcer
        self._loop = loop_detector
        self._planner = planner

    async def run_tick(self, *, org_id, tenant_id, autonomy_level, org_settings,
                       monthly_budget_usd, org_goals, org_mission) -> dict[str, int]:
        tick_id = uuid.uuid4().hex[:12]
        settings = resolve_autonomy_settings(org_settings, monthly_budget_usd)
        result = {"proposed": 0, "executed": 0, "blocked": 0}

        # SENSE
        try:
            health = await self._svc.get_org_health(str(org_id))
            spend, count, since = await self._counters.snapshot()
            counters_ok = True
        except Exception as exc:  # fail-closed: no acting this tick
            _log.warning("org_brain_sense_failed", org_id=str(org_id), error=str(exc)[:120])
            return result
        tc = health.get("task_counts", {}) if isinstance(health, dict) else {}
        snapshot = OrgSnapshot(
            blocked=int(tc.get("blocked", 0)), failed=int(tc.get("failed", 0)),
            active_missions=int(tc.get("active", 0) + tc.get("in_progress", 0)),
            open_goals=[str(g) for g in (org_goals or [])], goals_in_flight=frozenset(),
        )
        ctx = {"mission": org_mission, "monthly_budget_usd": monthly_budget_usd}

        # DECIDE
        decisions = decide(snapshot, settings, propose_goal_mission=lambda g: self._planner(g, ctx))

        # GUARD + ACT + NARRATE
        for d in decisions:
            verdict = evaluate_guardrails(
                d, autonomy_level=int(autonomy_level), settings=settings,
                counters=TickCounters(day_spend_usd=spend, missions_today=count,
                                      active_autonomous=snapshot.active_missions,
                                      seconds_since_last_launch=since,
                                      recent_signatures=frozenset(), counters_available=counters_ok),
                kill_switch=False, enforcer=self._enforcer, loop_detector=self._loop,
            )
            action, mission_id = "none", None
            if verdict.action == "execute":
                mission, _ = await self._svc.create_mission_and_execute(
                    org_id=str(org_id), title=d.rationale[:120], objective=d.rationale,
                    source="autonomous", budget_usd=d.est_cost_usd)
                await self._counters.record_launch(d.est_cost_usd)
                action, mission_id = "executed", getattr(mission, "id", None)
                result["executed"] += 1
            elif verdict.action == "propose":
                mission = await self._svc.create_mission(
                    org_id=str(org_id), title=d.rationale[:120], objective=d.rationale,
                    source="autonomous", status="proposed", budget_usd=d.est_cost_usd)
                action, mission_id = "proposed", getattr(mission, "id", None)
                result["proposed"] += 1
            else:
                action = "blocked"
                result["blocked"] += 1
            await self._store.record(
                org_id=org_id, tenant_id=tenant_id, tick_id=tick_id, kind=d.kind,
                rationale=d.rationale, target_goal=d.target_goal, action=action,
                guardrail_verdict=verdict.action, reason=verdict.reason,
                est_cost_usd=d.est_cost_usd, mission_id=mission_id)
        return result
```

- [ ] **Step 5: Run tests + lint/type** — `uv run pytest tests/org/test_brain_service.py tests/org/test_create_mission_status.py -q && uv run ruff check app/org/brain.py && uv run mypy app/org/brain.py` → PASS.

- [ ] **Step 6: Commit**

```bash
git add app/org/brain.py app/org/service.py tests/org/test_brain_service.py tests/org/test_create_mission_status.py
git commit -m "feat(org-brain): OrgBrain run_tick (SENSE/DECIDE/GUARD/ACT/NARRATE) + proposed missions"
```

---

### Task 7: Planner adapter over `goal_refinement`

Concrete `planner(goal, ctx)` that calls `goal_refinement` and maps its output to `(rationale, est_cost_usd, risk_level)`; returns None on any failure (so the brain degrades to no-proposal).

**Files:**
- Create: `agent-verse-backend/app/org/brain_planner.py`
- Test: `agent-verse-backend/tests/org/test_brain_planner.py`

**Interfaces:**
- Consumes: `goal_refinement` (inspect its public entry — a function/class that proposes a mission from a goal string + org context; it already estimates `estimated_budget_usd`).
- Produces: `make_planner(refiner) -> Callable[[str, dict], tuple[str,float,str]|None]`.

- [ ] **Step 1: Inspect `goal_refinement` entry point**

Run: `uv run python -c "import app.org.goal_refinement as g; print([n for n in dir(g) if not n.startswith('_')])"` and read the top of `app/org/goal_refinement.py` to find the propose function/class + its return type (fields include `estimated_budget_usd`). Note the exact name for Step 3.

- [ ] **Step 2: Write the failing test (fake refiner)**

```python
# tests/org/test_brain_planner.py
from app.org.brain_planner import make_planner


class _Refined:
    def __init__(self):
        self.title = "Advance g1"
        self.estimated_budget_usd = 7.5
        self.risk_level = "low"


class _FakeRefiner:
    def refine(self, goal, org_context):   # adjust to real method name in Step 3
        return _Refined()


def test_planner_maps_refinement_output():
    plan = make_planner(_FakeRefiner())
    out = plan("g1", {"mission": "grow"})
    assert out is not None
    rationale, cost, risk = out
    assert "g1" in rationale.lower() or "advance" in rationale.lower()
    assert cost == 7.5 and risk == "low"


def test_planner_returns_none_on_error():
    class _Boom:
        def refine(self, goal, org_context):
            raise RuntimeError("llm down")
    assert make_planner(_Boom())("g1", {}) is None
```

- [ ] **Step 3: Run test to verify it fails**, then implement mapping to the **real** `goal_refinement` method discovered in Step 1 (replace `.refine(...)`/field names as needed; keep the None-on-exception contract). Re-run.

- [ ] **Step 4: Lint/type + commit**

```bash
git add app/org/brain_planner.py tests/org/test_brain_planner.py
git commit -m "feat(org-brain): bounded planner adapter over goal_refinement (None on failure)"
```

---

### Task 8: Wire `OrgBrain` into `org_brain_loop`

Replace the stub body's counting with a real per-org tick, guarded by the feature flag, the tick lock, and cadence.

**Files:**
- Modify: `agent-verse-backend/app/scaling/tasks.py` (`org_brain_loop`, ~L5076)
- Test: `agent-verse-backend/tests/org/test_brain_loop_wiring.py`

**Interfaces:**
- Consumes everything above. Builds `OrgBrain` with real `OrgService(session, tenant_id)`, `BrainDecisionStore(session)`, `BrainCounters(redis, org_id)`, `AutonomyEnforcer()`, `LoopDetector()`, `make_planner(<goal_refinement>)`. Skips orgs where `not is_feature_enabled("org_autonomy_enabled", tenant_id)` or `autonomy_level < 3` (cheap short-circuit) or the tick lock is held.

- [ ] **Step 1: Write the failing test** (monkeypatch `OrgBrain.run_tick` to record calls; feed a fake org list) asserting: (a) an L1 org is skipped, (b) an L4 org with the flag on triggers `run_tick`, (c) a flag-off tenant is skipped. Use dependency seams already present in the loop (it reads `app.state.db_factory`, iterates `Organization`), monkeypatching the session/query or extracting the per-org body into a helper `async def _brain_tick_for_org(...)` that the test calls directly.

- [ ] **Step 2: Run → FAIL.**

- [ ] **Step 3: Refactor `org_brain_loop`** to call `_brain_tick_for_org(...)` per active org (keeping the existing per-org try/except isolation), short-circuiting on flag/level/lock; wire the real dependencies. Keep the return shape `{"processed","triggered", ...}` plus new `{"proposed","executed","blocked"}` sums.

- [ ] **Step 4: Run tests + full org suite + lint/type**

Run: `uv run pytest tests/org/ -q && uv run ruff check app/scaling/tasks.py && uv run mypy app/scaling/tasks.py`
Expected: PASS (no regressions in existing org tests).

- [ ] **Step 5: Commit**

```bash
git add app/scaling/tasks.py tests/org/test_brain_loop_wiring.py
git commit -m "feat(org-brain): drive OrgBrain from org_brain_loop (flag+level+lock gated)"
```

---

# Phase 3 — Ambient collaboration tick

### Task 9: Collaboration tick (capped lead chatter → events)

**Files:**
- Create: `agent-verse-backend/app/org/brain_collaboration.py`
- Modify: `agent-verse-backend/app/scaling/tasks.py` (new beat task `org_collaboration_loop`) + `app/scaling/celery_app.py` (schedule it, e.g. every 900s)
- Test: `agent-verse-backend/tests/org/test_brain_collaboration.py`

**Interfaces:**
- Produces: `CollaborationTick(model_gateway, event_publisher, counters)` with `async run(*, org_id, tenant_id, settings:AutonomySettings, autonomy_level:int, leads:list[str], day_spend_usd:float) -> int` (messages emitted). Guards: returns 0 if `not settings.collaboration_enabled`, `autonomy_level < 3`, or `day_spend_usd >= settings.daily_budget_usd * 0.9`; caps at `settings.collab_messages_per_tick`; each message uses a short token budget via `model_gateway`; emits `org.collaboration.message` events.

- [ ] **Step 1: Write the failing test** (fake model_gateway returning a short line; fake event_publisher collecting events). Assert: disabled → 0 messages/no events; enabled+L4 → ≤ cap messages emitted as `org.collaboration.message`; budget-mostly-spent → 0.

- [ ] **Step 2: Run → FAIL.**

- [ ] **Step 3: Implement** with strict caps + the guards above (fail-closed: any model error → stop the tick, emit nothing further).

- [ ] **Step 4: Schedule** `org_collaboration_loop` in `celery_app.py` beat_schedule (every 900s, queue `maintenance`); it iterates active L3+ orgs with `collaboration_enabled` and calls `CollaborationTick.run(...)`.

- [ ] **Step 5: Run tests + lint/type + commit**

```bash
git add app/org/brain_collaboration.py app/scaling/tasks.py app/scaling/celery_app.py tests/org/test_brain_collaboration.py
git commit -m "feat(org-brain): capped ambient collaboration tick emitting team-channel events"
```

---

# Phase 4 — API + Frontend surfacing

### Task 10: API — autonomy settings + brain decisions + approve endpoints

**Files:**
- Modify: `agent-verse-backend/app/org/router.py`
- Test: `agent-verse-backend/tests/org/test_brain_router.py`

**Interfaces (new endpoints, mounted under the org router):**
- `GET  /orgs/{org_id}/autonomy` → `{ autonomy_level, settings: <AutonomySettings as dict> }`.
- `PATCH /orgs/{org_id}/autonomy` body `{ autonomy_level?, settings? }` → persists to `Organization.autonomy_level` + merges into `Organization.settings["autonomy"]`; returns the resolved view. (Bound `autonomy_level` to 0..5.)
- `GET  /orgs/{org_id}/brain/decisions?limit=50` → `BrainDecisionStore.list(...)`.
- `POST /orgs/{org_id}/brain/proposals/{mission_id}/approve` → transitions a `proposed` autonomous mission to executing via the existing dispatch path (`create_mission_and_execute`-equivalent or the existing mission dispatch used by ApprovalCenter); `POST .../reject` → sets mission `status="cancelled"`.

- [ ] **Step 1: Write the failing test** (FastAPI `TestClient`, in-memory/app-state org service, seeded org) covering: PATCH sets level+caps and GET reflects them (with derived budgets); GET decisions returns recorded rows; approve promotes a proposed mission. Mirror an existing `tests/org/test_*router*.py` for app construction/fixtures.
- [ ] **Step 2: Run → FAIL.**
- [ ] **Step 3: Implement** the endpoints; reuse existing tenant/auth dependencies and the `OrgService` from `request.app.state`.
- [ ] **Step 4: Run tests + lint/type + regenerate OpenAPI** (`uv run python scripts/export_openapi.py`) + commit.

```bash
git add app/org/router.py tests/org/test_brain_router.py <openapi output path>
git commit -m "feat(org-brain): autonomy settings + brain decisions + proposal approve API"
```

---

### Task 11: Frontend — API client + types

**Files:**
- Modify: `agent-verse-frontend/src/lib/api/client.ts`
- Test: `agent-verse-frontend/src/features/org/__tests__/orgAutonomyApi.test.ts`

**Interfaces:**
- Produces on `client.ts`: types `AutonomySettings`, `BrainDecision`; `orgAutonomyApi = { get(orgId), patch(orgId, body), decisions(orgId, limit?), approveProposal(orgId, missionId), rejectProposal(orgId, missionId) }` hitting the Task 10 routes under the `/api/v1` proxy.

- [ ] **Step 1: Write a vitest** asserting `orgAutonomyApi.patch` issues `PATCH /api/v1/orgs/{id}/autonomy` with the JSON body (mock `fetch`/the shared `request`), and `decisions` issues the GET. Mirror an existing client test.
- [ ] **Step 2: Run → FAIL.**  **Step 3: Implement** the client functions + types.  **Step 4: `npm run typecheck && npm run test -- src/features/org` → PASS. Commit.**

```bash
git add src/lib/api/client.ts src/features/org/__tests__/orgAutonomyApi.test.ts
git commit -m "feat(org-ui): org autonomy API client + types"
```

---

### Task 12: Frontend — Autonomy control panel

**Files:**
- Create: `agent-verse-frontend/src/features/org/components/AutonomyControl.tsx`
- Test: `agent-verse-frontend/src/features/org/__tests__/AutonomyControl.test.tsx`

**Interfaces:** `AutonomyControl({ orgId }: { orgId: string })` — level selector L1–L5, prominent **Pause** toggle, caps (daily budget, max concurrent, max/day, cadence), collaboration toggle + budget. Reads via `orgAutonomyApi.get`, writes via `orgAutonomyApi.patch` (TanStack Query mutation + invalidate). Uses existing JARVIS/Tailwind styling.

- [ ] **Step 1: Write a vitest + React Testing Library test**: renders current level + a Pause toggle; clicking Pause calls `orgAutonomyApi.patch` with `{ settings: { paused: true } }` (mock the api module). 
- [ ] **Step 2: Run → FAIL.**  **Step 3: Implement.**  **Step 4: typecheck + test → PASS. Commit.**

```bash
git add src/features/org/components/AutonomyControl.tsx src/features/org/__tests__/AutonomyControl.test.tsx
git commit -m "feat(org-ui): autonomy control panel (level, pause, caps, collaboration)"
```

---

### Task 13: Frontend — Brain feed + Team channel, wired into OrgPage

**Files:**
- Create: `agent-verse-frontend/src/features/org/components/BrainFeed.tsx`
- Create: `agent-verse-frontend/src/features/org/components/TeamChannel.tsx`
- Modify: `agent-verse-frontend/src/features/org/OrgPage.tsx` (mount AutonomyControl + BrainFeed in the command panel; TeamChannel consumes `org.collaboration.message` from the existing org event stream / ActivityFeed source)
- Test: `agent-verse-frontend/src/features/org/__tests__/BrainFeed.test.tsx`

**Interfaces:** `BrainFeed({ orgId })` lists `orgAutonomyApi.decisions(orgId)` as a timeline showing action + reason (including blocked/held-back, with a distinct style). `TeamChannel({ orgId })` renders collaboration messages from the org event stream.

- [ ] **Step 1: Write a vitest** for `BrainFeed`: given mocked decisions (`executed`, `blocked`), it renders both rows and shows the block reason.  
- [ ] **Step 2: Run → FAIL.**  **Step 3: Implement** BrainFeed + TeamChannel; mount into OrgPage's right command panel (place BrainFeed under AutonomyControl; TeamChannel near the ActivityFeed).  **Step 4: `npm run typecheck && npm run test -- src/features/org && npm run build` → PASS.**
- [ ] **Step 5: Commit.**

```bash
git add src/features/org/components/BrainFeed.tsx src/features/org/components/TeamChannel.tsx src/features/org/OrgPage.tsx src/features/org/__tests__/BrainFeed.test.tsx
git commit -m "feat(org-ui): brain decisions feed + team channel wired into org command center"
```

---

### Task 14: End-to-end verification (safe, opt-in)

**Files:** none (verification + docs).

- [ ] **Step 1:** `colima start`; `docker-compose -f infra/docker-compose.yml up -d postgres redis`; `uv run alembic upgrade head`.
- [ ] **Step 2:** Full suites: `uv run pytest tests/org -q` and (frontend) `npm run test -- src/features/org`, `npm run typecheck`, `npm run build` → all green.
- [ ] **Step 3:** Manual smoke: create/seed an org, set a `goals` entry + a small `monthly_budget_usd`, set `autonomy_level=4` and `org_autonomy_enabled` flag on; run one beat cycle (or invoke `_brain_tick_for_org` directly); confirm a mission is created with `source="autonomous"`, a decision row is written, the Brain Feed shows it, and the daily-budget/concurrency caps hold on a second tick (blocked). Flip **Pause** → next tick is a no-op.
- [ ] **Step 4:** Update `docs/superpowers/specs/2026-09-14-autonomous-org-brain-design.md` status to "Implemented (v1)"; commit.

```bash
git add docs/superpowers/specs/2026-09-14-autonomous-org-brain-design.md
git commit -m "docs(org-brain): mark AOB v1 implemented after e2e verification"
```

---

## Self-review (author checklist — completed)

- **Spec coverage:** brain tick (T5,T6,T8) · guardrail gate all 8 checks (T2) · autonomy levels L1–L5 (T2 baseline + T6 ACT) · cost/concurrency/cooldown/dedup/kill-switch caps (T2+T4) · fail-closed (T2,T6) · audit trail (T3) · ambient collaboration (T9) · surfacing incl. held-back reasons + pause (T10–T13) · safe defaults/flag/env kill switch (Global Constraints, T1, T8) · testing (every task). All spec sections map to tasks.
- **Placeholder scan:** the two spots requiring codebase discovery (T3 migration head, T7 `goal_refinement` entry name) are explicit *inspect-then-fill* steps with commands, not hand-waves; all core logic ships real code.
- **Type consistency:** `BrainDecision`, `TickCounters`, `Verdict`, `AutonomySettings`, `OrgSnapshot` names/fields are identical across T1–T8; `evaluate_guardrails` / `decide` / `run_tick` signatures match their consumers; `create_mission(status=...)` added in T6-Step1 before its use.
