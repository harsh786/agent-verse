"""Org Digital Twin — SUPPLEMENT H.

A read-only model of the organisation used for planning and capacity analysis
WITHOUT modifying production state.

Every number the twin returns is derived from real org data read through the
request's RLS-scoped :class:`~app.org.service.OrgService`:

* **Department utilisation** = staffed agents with in-flight work / staffed
  agents, where "staffed" means a member or manager of one of the department's
  active teams and "in-flight" means referenced by a task in one of
  ``ACTIVE_TASK_STATUSES``. A department with no staffed agents has no
  measurable utilisation: it reports ``None`` plus a ``reason``.
* **Mission estimates** = median wall-clock duration / recorded task cost of
  completed missions at the same priority. With no such history the estimate is
  ``None`` plus a reason. There is no calibrated confidence model, so
  ``confidence`` is always ``None`` and ``sample_size`` is reported instead.
* **What-if** re-simulation is not implemented; :meth:`OrgDigitalTwin.what_if`
  raises :class:`WhatIfNotSupportedError` (the API maps it to HTTP 501).

These previously were a hash of the department name, a priority lookup table,
and a canned "15-20% throughput gain" string.
"""

from __future__ import annotations

import statistics
from collections.abc import Awaitable, Callable, Iterable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

import structlog
from opentelemetry import trace

_log = structlog.get_logger(__name__)
_tracer = trace.get_tracer(__name__)

# Task statuses in which an assigned agent is occupied by the task.
ACTIVE_TASK_STATUSES: tuple[str, ...] = (
    "assigned",
    "running",
    "waiting",
    "blocked",
    "review",
    "approval_required",
)
# Task statuses that represent accepted-but-not-started work.
QUEUED_TASK_STATUSES: tuple[str, ...] = ("queued", "planned")

OVERLOADED_THRESHOLD = 0.85
UNDERUTILISED_THRESHOLD = 0.35

# Page size for exhaustive reads through OrgService list_* methods.
_PAGE_SIZE = 500
# Completed missions sampled per simulation (newest first).
_HISTORY_SAMPLE = 20
# Tasks read per sampled mission (matches service.MAX_TASKS_PER_MISSION).
_TASKS_PER_MISSION = 200

NO_AGENTS_REASON = (
    "No agents are staffed on this department's active teams, so utilisation "
    "cannot be measured."
)
CLEAR_TIME_REASON = (
    "Time-to-clear is not estimated: the twin has no throughput model for queued work."
)
NO_DATA_SOURCE_REASON = "No org data source is available to base an estimate on."
_NO_AGENTS_BOTTLENECK = "No agents are staffed on any active team."


class WhatIfNotSupportedError(NotImplementedError):
    """What-if re-simulation is not implemented; never fabricate a projection."""


@dataclass
class SimResult:
    """Result of a digital twin mission simulation.

    ``None`` means "not enough real data to say" — see ``estimate_reason``.
    """

    mission_id: str | None = None
    estimated_duration_h: float | None = None
    estimated_cost_usd: float | None = None
    resource_usage: dict[str, float] = field(default_factory=dict)
    bottlenecks: list[str] = field(default_factory=list)
    recommendations: list[str] = field(default_factory=list)
    feasible: bool | None = None
    confidence: float | None = None
    sample_size: int = 0
    estimate_reason: str | None = None
    simulated_at: str = ""

    def __post_init__(self) -> None:
        if not self.simulated_at:
            self.simulated_at = datetime.now(UTC).isoformat()


@dataclass
class DepartmentUtilisation:
    """Measured utilisation of one department (``None`` + reason if unmeasurable)."""

    dept_id: str
    name: str
    agent_count: int
    busy_agent_count: int
    active_task_count: int
    queued_task_count: int
    utilisation: float | None
    reason: str | None = None


@dataclass
class CapacityPlan:
    """Capacity planning output."""

    org_id: str
    departments: list[DepartmentUtilisation]
    current_utilisation: dict[str, float | None]  # department name → fraction or None
    queued_missions: int
    estimated_clear_h: float | None
    estimated_clear_reason: str | None
    underutilised: list[str]
    overloaded: list[str]
    recommendations: list[str]


@dataclass
class _Staffing:
    teams_by_dept: dict[str, list[Any]]
    agents_by_team: dict[str, set[str]]
    all_agents: set[str]
    busy_agents: set[str]
    active_tasks: list[Any]
    queued_tasks: list[Any]


async def _read_all(fetch: Callable[[int, int], Awaitable[list[Any]]]) -> list[Any]:
    """Drain a paginated ``list_*`` read (``fetch(limit, offset)``)."""
    rows: list[Any] = []
    offset = 0
    while True:
        page = list(await fetch(_PAGE_SIZE, offset))
        rows.extend(page)
        if len(page) < _PAGE_SIZE:
            return rows
        offset += _PAGE_SIZE


async def _tasks_in(service: Any, org_id: str, statuses: Iterable[str]) -> list[Any]:
    tasks: list[Any] = []
    for status in statuses:

        async def _fetch(limit: int, offset: int, _s: str = status) -> list[Any]:
            return list(await service.list_tasks(org_id, status=_s, limit=limit, offset=offset))

        tasks.extend(await _read_all(_fetch))
    return tasks


def _team_agents(team: Any) -> set[str]:
    agents = {str(a) for a in (getattr(team, "member_agent_ids", None) or []) if a}
    manager = getattr(team, "manager_agent_id", None)
    if manager:
        agents.add(str(manager))
    return agents


def _task_agents(task: Any) -> set[str]:
    agents = {str(a) for a in (getattr(task, "assigned_agent_ids", None) or []) if a}
    owner = getattr(task, "owner_agent_id", None)
    if owner:
        agents.add(str(owner))
    return agents


async def _staffing(service: Any, org_id: str) -> _Staffing:
    teams = list(await service.list_teams(org_id, status="active"))
    teams_by_dept: dict[str, list[Any]] = {}
    agents_by_team: dict[str, set[str]] = {}
    all_agents: set[str] = set()
    for team in teams:
        agents = _team_agents(team)
        agents_by_team[str(team.id)] = agents
        all_agents |= agents
        dept_id = getattr(team, "dept_id", None)
        if dept_id is not None:
            teams_by_dept.setdefault(str(dept_id), []).append(team)

    active_tasks = await _tasks_in(service, org_id, ACTIVE_TASK_STATUSES)
    queued_tasks = await _tasks_in(service, org_id, QUEUED_TASK_STATUSES)
    busy: set[str] = set()
    for task in active_tasks:
        busy |= _task_agents(task)
    return _Staffing(
        teams_by_dept=teams_by_dept,
        agents_by_team=agents_by_team,
        all_agents=all_agents,
        busy_agents=busy,
        active_tasks=active_tasks,
        queued_tasks=queued_tasks,
    )


def _count_for_teams(tasks: list[Any], team_ids: set[str]) -> int:
    return sum(
        1
        for t in tasks
        if getattr(t, "assigned_team_id", None) is not None and str(t.assigned_team_id) in team_ids
    )


def _pct(value: float | None) -> str:
    return f"{(value or 0.0):.0%}"


class OrgDigitalTwin:
    """Read-only model of the organisation.

    Purposes:
    1. Planning — "What does a mission like this usually take here?"
    2. Capacity — "How loaded is each department right now?"
    3. What-if  — not implemented (raises :class:`WhatIfNotSupportedError`).

    Never modifies production state — every read goes through the caller's
    RLS-scoped ``OrgService``.
    """

    def __init__(self) -> None:
        self._db: Any = None  # async session factory (injected in lifespan)
        self._last_synced_event: dict[str, dict[str, Any]] = {}  # org_id -> last event
        self._synced_event_counts: dict[str, int] = {}  # org_id -> total events synced

    def set_db(self, db_factory: Any) -> None:
        self._db = db_factory

    async def sync(self, event: dict[str, Any]) -> None:
        """Record the latest real org event in the twin's in-memory snapshot.

        Called by the org-twin-sync trigger (app/org/feature_flags.py). This
        never touches production state.
        """
        org_id = event.get("org_id", "")
        if not org_id:
            return
        with _tracer.start_as_current_span("digital_twin.sync") as span:
            span.set_attribute("org_id", org_id)
            span.set_attribute("event_type", event.get("event_type", ""))
            self._last_synced_event[org_id] = event
            self._synced_event_counts[org_id] = self._synced_event_counts.get(org_id, 0) + 1
            _log.debug(
                "digital_twin.synced",
                org_id=org_id,
                event_type=event.get("event_type", ""),
            )

    async def simulate_mission(
        self,
        org_id: str,
        mission_config: dict[str, Any],
        service: Any = None,
    ) -> SimResult:
        """Estimate a mission from this org's real history and staffing.

        Duration/cost are medians over completed missions of the same priority;
        feasibility checks required capabilities against the org's registered
        capabilities and that at least one agent is staffed. Without history
        the estimates are ``None`` with an ``estimate_reason``.
        """
        with _tracer.start_as_current_span("digital_twin.simulate_mission") as span:
            span.set_attribute("org_id", org_id)
            title = mission_config.get("title", "Unknown")
            priority = str(mission_config.get("priority") or "medium")
            span.set_attribute("mission_title", title)
            span.set_attribute("priority", priority)
            _log.info("digital_twin.simulate_mission", org_id=org_id, title=title)

            if service is None:
                return SimResult(estimate_reason=NO_DATA_SOURCE_REASON)

            # ── history: completed missions at this priority ─────────────────
            history = list(
                await service.list_missions(
                    org_id, status="completed", priority=priority, limit=_HISTORY_SAMPLE
                )
            )
            durations: list[float] = []
            costs: list[float] = []
            for mission in history:
                started = getattr(mission, "started_at", None)
                completed = getattr(mission, "completed_at", None)
                if started is None or completed is None:
                    continue
                durations.append((completed - started).total_seconds() / 3600.0)
                tasks = await service.list_tasks(
                    org_id, mission_id=str(mission.id), limit=_TASKS_PER_MISSION
                )
                recorded = [
                    float(t.actual_cost_usd)
                    for t in tasks
                    if getattr(t, "actual_cost_usd", None) is not None
                ]
                if recorded:
                    costs.append(sum(recorded))

            duration_h = round(statistics.median(durations), 2) if durations else None
            cost_usd = round(statistics.median(costs), 4) if costs else None
            reason: str | None = None
            if duration_h is None:
                reason = (
                    f"No completed '{priority}'-priority missions with recorded start and "
                    "finish times in this org yet, so duration and cost are not estimated."
                )
            elif cost_usd is None:
                reason = (
                    "No task costs are recorded on comparable missions, so cost is not estimated."
                )

            # ── feasibility: capabilities + staffing ─────────────────────────
            staffing = await _staffing(service, org_id)
            staffed = len(staffing.all_agents)
            busy = len(staffing.all_agents & staffing.busy_agents)
            blocking: list[str] = []
            required = [str(c) for c in (mission_config.get("required_capabilities") or []) if c]
            if required:
                caps = await service.list_capabilities(org_id)
                known = {str(getattr(c, "name", "")).lower() for c in caps}
                blocking.extend(
                    f"Capability '{c}' is not registered in this org."
                    for c in required
                    if c.lower() not in known
                )
            if staffed == 0:
                blocking.append(_NO_AGENTS_BOTTLENECK)
            bottlenecks = list(blocking)
            if staffed and busy >= staffed:
                bottlenecks.append("Every staffed agent is currently busy.")

            recommendations: list[str] = []
            if duration_h is None:
                recommendations.append(
                    f"Complete missions at '{priority}' priority to enable estimates."
                )
            recommendations.extend(f"Resolve: {b}" for b in bottlenecks)

            result = SimResult(
                estimated_duration_h=duration_h,
                estimated_cost_usd=cost_usd,
                resource_usage={
                    "staffed_agents": staffed,
                    "busy_agents": busy,
                    "available_agents": staffed - busy,
                },
                bottlenecks=bottlenecks,
                recommendations=recommendations,
                feasible=not blocking,
                confidence=None,
                sample_size=len(durations),
                estimate_reason=reason,
            )
            span.set_attribute("sample_size", result.sample_size)
            return result

    async def capacity_plan(self, org_id: str, service: Any) -> CapacityPlan:
        """Measure per-department utilisation from real staffing and tasks."""
        with _tracer.start_as_current_span("digital_twin.capacity_plan") as span:
            span.set_attribute("org_id", org_id)

            depts = list(await service.list_departments(org_id))
            staffing = await _staffing(service, org_id)

            departments: list[DepartmentUtilisation] = []
            for dept in depts:
                team_ids = {str(t.id) for t in staffing.teams_by_dept.get(str(dept.id), [])}
                agents: set[str] = set()
                for tid in team_ids:
                    agents |= staffing.agents_by_team.get(tid, set())
                busy = agents & staffing.busy_agents
                departments.append(
                    DepartmentUtilisation(
                        dept_id=str(dept.id),
                        name=str(dept.name),
                        agent_count=len(agents),
                        busy_agent_count=len(busy),
                        active_task_count=_count_for_teams(staffing.active_tasks, team_ids),
                        queued_task_count=_count_for_teams(staffing.queued_tasks, team_ids),
                        utilisation=round(len(busy) / len(agents), 4) if agents else None,
                        reason=None if agents else NO_AGENTS_REASON,
                    )
                )

            async def _fetch_queued(limit: int, offset: int) -> list[Any]:
                return list(
                    await service.list_missions(org_id, status="queued", limit=limit, offset=offset)
                )

            queued_missions = len(await _read_all(_fetch_queued))

            util_by_name = {d.name: d.utilisation for d in departments}
            overloaded = [
                d.name
                for d in departments
                if d.utilisation is not None and d.utilisation > OVERLOADED_THRESHOLD
            ]
            underutilised = [
                d.name
                for d in departments
                if d.utilisation is not None and d.utilisation < UNDERUTILISED_THRESHOLD
            ]
            recommendations = [
                f"Redistribute work from {n} — at {_pct(util_by_name[n])} capacity."
                for n in overloaded[:2]
            ] + [
                f"{n} is underutilised ({_pct(util_by_name[n])}) — assign more work."
                for n in underutilised[:2]
            ]

            span.set_attribute("departments", len(departments))
            span.set_attribute("overloaded", len(overloaded))
            return CapacityPlan(
                org_id=org_id,
                departments=departments,
                current_utilisation=util_by_name,
                queued_missions=queued_missions,
                estimated_clear_h=None,
                estimated_clear_reason=CLEAR_TIME_REASON,
                underutilised=underutilised,
                overloaded=overloaded,
                recommendations=recommendations,
            )

    async def what_if(
        self,
        org_id: str,
        scenario: dict[str, Any],
    ) -> dict[str, Any]:
        """What-if analysis is not implemented — raise instead of inventing a gain."""
        with _tracer.start_as_current_span("digital_twin.what_if") as span:
            span.set_attribute("org_id", org_id)
            span.set_attribute("scenario_keys", str(list(scenario.keys())))
            raise WhatIfNotSupportedError(
                "What-if re-simulation is not implemented: the digital twin has no "
                "throughput model to project scenario outcomes from."
            )


# Module-level singleton (wired in lifespan if needed)
_twin = OrgDigitalTwin()


def get_twin() -> OrgDigitalTwin:
    """Return the process-local digital twin instance."""
    return _twin
