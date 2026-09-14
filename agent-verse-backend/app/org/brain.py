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

# dispatcher(kwargs) -> enqueue the created mission for real execution on the
# worker-wired path (mirrors ``execute_org_mission.apply_async(kwargs=...)`` —
# see ``app/scaling/tasks.py``). Synchronous/fire-and-forget: it only has to
# publish to the broker, not await anything. Injected so OrgBrain stays
# unit-testable with a fake (no Celery/Redis needed); production wiring
# (``app/scaling/tasks.py::_brain_tick_for_org``) binds the real task.
Dispatcher = Callable[[dict[str, Any]], Any]

# Mission statuses that mean "this autonomous mission is done and can no
# longer collide with a new one" — everything else (draft/queued/planned/
# active/paused/review/proposed) is still in flight for dedup purposes.
_TERMINAL_MISSION_STATUSES = ("completed", "failed", "cancelled", "archived")


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
        dispatcher: Dispatcher | None = None,
    ) -> None:
        self._svc = org_service
        self._store = brain_store
        self._counters = counters
        self._enforcer = enforcer
        self._loop = loop_detector
        self._planner = planner
        self._dispatch = dispatcher

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
            # Real in-flight autonomous missions for THIS org+tenant, so DECIDE's
            # goal dedup and GUARD's signature dedup (#6) get actual data instead
            # of the empty sets they used to be fed. We stamp ``target_goal`` +
            # ``signature`` into ``trigger_event`` when we create these missions
            # below (both execute and propose branches), so the strings compared
            # here are exactly the ones DECIDE/GUARD compare against — no
            # re-derivation, no format drift.
            in_flight_missions = await self._svc.list_missions(
                str(org_id),
                source="autonomous",
                exclude_statuses=list(_TERMINAL_MISSION_STATUSES),
                limit=200,
            )
            counters_ok = True
        except Exception as exc:  # fail-closed: no acting this tick on a sense failure
            _log.warning("org_brain_sense_failed", org_id=str(org_id), error=str(exc)[:120])
            return result

        goals_in_flight: set[str] = set()
        recent_signatures: set[str] = set()
        for m in in_flight_missions or []:
            trig = getattr(m, "trigger_event", None)
            if not isinstance(trig, dict):
                continue
            goal = trig.get("target_goal")
            if goal:
                goals_in_flight.add(str(goal))
            sig = trig.get("signature")
            if sig:
                recent_signatures.add(str(sig))

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
            goals_in_flight=frozenset(goals_in_flight),
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
                    recent_signatures=frozenset(recent_signatures),
                    counters_available=counters_ok,
                ),
                kill_switch=False,
                enforcer=self._enforcer,
                loop_detector=self._loop,
            )

            action, mission_id = "blocked", None
            if verdict.action == "execute":
                # Persist the mission as "planned" (same status the SCHEDULE
                # and resweep paths use) and stamp target_goal/signature into
                # trigger_event so the NEXT tick's SENSE step can dedup against
                # it. Then dispatch it on the properly-wired worker path —
                # ``execute_org_mission`` (same Celery task the SCHEDULE path
                # enqueues via ``fire_due_org_mission_schedules`` and the
                # crash-recovery resweep re-enqueues) — instead of calling
                # ``create_mission_and_execute`` in-process, which has no
                # wired ``app_state``/``GoalService`` here in the Celery beat
                # loop and would strand the mission at "planned" until the
                # 3-minute resweep happened to pick it up.
                mission = await self._svc.create_mission(
                    org_id=str(org_id),
                    title=d.rationale[:120],
                    objective=d.rationale,
                    source="autonomous",
                    status="planned",
                    autonomy_level=int(autonomy_level),
                    budget_usd=d.est_cost_usd,
                    trigger_event={"target_goal": d.target_goal, "signature": d.signature},
                )
                await self._counters.record_launch(d.est_cost_usd)
                mission_id = getattr(mission, "id", None)
                if self._dispatch is not None and mission_id is not None:
                    try:
                        self._dispatch(
                            {
                                "mission_id": str(mission_id),
                                "tenant_id": str(tenant_id),
                                "org_id": str(org_id),
                                "objective": d.rationale,
                                "title": d.rationale[:120],
                                "autonomy_level": int(autonomy_level),
                                "priority": "medium",
                            }
                        )
                    except Exception as exc:
                        # Fail safe, not fail silent-forever: the mission stays
                        # "planned" and the 3-minute resweep still picks it up.
                        _log.warning(
                            "org_brain_dispatch_failed",
                            org_id=str(org_id),
                            mission_id=str(mission_id),
                            error=str(exc)[:120],
                        )
                action = "executed"
                result["executed"] += 1
            elif verdict.action == "propose":
                mission = await self._svc.create_mission(
                    org_id=str(org_id),
                    title=d.rationale[:120],
                    objective=d.rationale,
                    source="autonomous",
                    status="proposed",
                    budget_usd=d.est_cost_usd,
                    trigger_event={"target_goal": d.target_goal, "signature": d.signature},
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
