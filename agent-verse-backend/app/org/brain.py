"""OrgBrain — the autonomous org brain's tick orchestrator (Task 6).

Wires the pure/typed pieces built in Tasks 1-5 into one coroutine,
``OrgBrain.run_tick``, that the Celery beat loop (Task 8) calls once per
organization per cadence tick:

    SENSE   → read org health + spend/launch counters (``OrgService``,
              injected ``counters``)
    DECIDE  → ``app.org.brain_decide.decide`` turns the snapshot into a list
              of candidate ``BrainDecision``s (reactive remediation +
              proactive charter pursuit), consulting the injected
              ``planner`` for proactive goal → (rationale, cost, risk)
    GUARD   → ``app.org.brain_guardrails.evaluate_guardrails`` is the single
              chokepoint deciding execute / propose / block per decision
    ACT     → dispatch the mission (execute), create it unstarted for human
              review (propose), or do nothing (block)
    NARRATE → ``brain_store.record`` persists every decision + verdict for
              the audit trail / UI regardless of outcome

All dependencies are constructor-injected so this is fully unit-testable
with fakes (see ``tests/org/test_brain_service.py``) — no DB or Redis
required at that layer.
"""

from __future__ import annotations

import uuid
from collections.abc import Callable
from typing import Any

from app.observability.logging import get_logger
from app.org.brain_decide import OrgSnapshot, decide
from app.org.brain_guardrails import TickCounters, evaluate_guardrails
from app.org.brain_settings import resolve_autonomy_settings

_log = get_logger(__name__)

# planner(goal, org_context) -> (rationale, est_cost_usd, risk_level) | None
Planner = Callable[[str, dict[str, Any]], "tuple[str, float, str] | None"]


class OrgBrain:
    """Runs one SENSE/DECIDE/GUARD/ACT/NARRATE tick for a single organization."""

    def __init__(
        self,
        *,
        org_service: Any,
        brain_store: Any,
        counters: Any,
        enforcer: Any,
        loop_detector: Any,
        planner: Planner,
    ) -> None:
        self._svc = org_service
        self._store = brain_store
        self._counters = counters
        self._enforcer = enforcer
        self._loop = loop_detector
        self._planner = planner

    async def run_tick(
        self,
        *,
        org_id: str,
        tenant_id: str,
        autonomy_level: int,
        org_settings: dict[str, Any] | None,
        monthly_budget_usd: float,
        org_goals: list[str] | None,
        org_mission: str,
    ) -> dict[str, int]:
        tick_id = uuid.uuid4().hex[:12]
        settings = resolve_autonomy_settings(org_settings, monthly_budget_usd)
        result = {"proposed": 0, "executed": 0, "blocked": 0}

        # ── SENSE ─────────────────────────────────────────────────────────────
        try:
            health = await self._svc.get_org_health(str(org_id))
            spend, count, since = await self._counters.snapshot()
            counters_ok = True
        except Exception as exc:  # fail-closed: no acting this tick on a sense failure
            _log.warning("org_brain_sense_failed", org_id=str(org_id), error=str(exc)[:120])
            return result

        tc = health.get("task_counts", {}) if isinstance(health, dict) else {}
        # NOTE: OrgService.get_org_health returns "active_missions" as a
        # top-level int (a count of OrgMission rows with status == "active"),
        # NOT inside task_counts — task_counts is keyed by OrgTask.status
        # values (e.g. "blocked", "failed", "running", ...), which have no
        # "active"/"in_progress" entries. Read the top-level key directly.
        active_missions = int(health.get("active_missions", 0)) if isinstance(health, dict) else 0
        snapshot = OrgSnapshot(
            blocked=int(tc.get("blocked", 0)),
            failed=int(tc.get("failed", 0)),
            active_missions=active_missions,
            open_goals=[str(g) for g in (org_goals or [])],
            goals_in_flight=frozenset(),
        )
        ctx = {"mission": org_mission, "monthly_budget_usd": monthly_budget_usd}

        # ── DECIDE ────────────────────────────────────────────────────────────
        decisions = decide(snapshot, settings, propose_goal_mission=lambda g: self._planner(g, ctx))

        # ── GUARD + ACT + NARRATE ────────────────────────────────────────────
        for d in decisions:
            verdict = evaluate_guardrails(
                d,
                autonomy_level=int(autonomy_level),
                settings=settings,
                counters=TickCounters(
                    day_spend_usd=spend,
                    missions_today=count,
                    active_autonomous=snapshot.active_missions,
                    seconds_since_last_launch=since,
                    recent_signatures=frozenset(),
                    counters_available=counters_ok,
                ),
                kill_switch=False,
                enforcer=self._enforcer,
                loop_detector=self._loop,
            )

            action, mission_id = "blocked", None
            if verdict.action == "execute":
                mission, _dispatch = await self._svc.create_mission_and_execute(
                    org_id=str(org_id),
                    title=d.rationale[:120],
                    objective=d.rationale,
                    source="autonomous",
                    budget_usd=d.est_cost_usd,
                )
                await self._counters.record_launch(d.est_cost_usd)
                action, mission_id = "executed", getattr(mission, "id", None)
                result["executed"] += 1
            elif verdict.action == "propose":
                mission = await self._svc.create_mission(
                    org_id=str(org_id),
                    title=d.rationale[:120],
                    objective=d.rationale,
                    source="autonomous",
                    status="proposed",
                    budget_usd=d.est_cost_usd,
                )
                action, mission_id = "proposed", getattr(mission, "id", None)
                result["proposed"] += 1
            else:
                result["blocked"] += 1

            await self._store.record(
                org_id=org_id,
                tenant_id=tenant_id,
                tick_id=tick_id,
                kind=d.kind,
                rationale=d.rationale,
                target_goal=d.target_goal,
                action=action,
                guardrail_verdict=verdict.action,
                reason=verdict.reason,
                est_cost_usd=d.est_cost_usd,
                mission_id=mission_id,
            )

        return result
