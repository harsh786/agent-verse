"""Tests for OrgDigitalTwin — app/org/digital_twin.py.

The twin must be grounded in real org data (departments → teams → staffed
agents, in-flight tasks, completed-mission history). Where that data does not
exist it returns honest ``None`` values with a reason — never a hash-derived,
priority-lookup, or canned number.
"""
from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

ORG_ID = str(uuid.uuid4())
ENG_ID = uuid.uuid4()
OPS_ID = uuid.uuid4()
LEGAL_ID = uuid.uuid4()
TEAM_ENG = uuid.uuid4()
TEAM_OPS = uuid.uuid4()


def _dept(dept_id: uuid.UUID, name: str) -> SimpleNamespace:
    return SimpleNamespace(id=dept_id, name=name)


def _team(
    team_id: uuid.UUID,
    dept_id: uuid.UUID | None,
    members: list[str],
    manager: str | None = None,
) -> SimpleNamespace:
    return SimpleNamespace(
        id=team_id, dept_id=dept_id, member_agent_ids=members, manager_agent_id=manager
    )


def _task(
    status: str,
    team_id: uuid.UUID | None,
    agents: list[str] | None = None,
    owner: str | None = None,
    actual_cost_usd: float | None = None,
) -> SimpleNamespace:
    return SimpleNamespace(
        status=status,
        assigned_team_id=team_id,
        assigned_agent_ids=agents or [],
        owner_agent_id=owner,
        actual_cost_usd=actual_cost_usd,
    )


def _fake_service(
    *,
    depts: list[Any] | None = None,
    teams: list[Any] | None = None,
    tasks: list[Any] | None = None,
    missions: list[Any] | None = None,
    capabilities: list[Any] | None = None,
    mission_tasks: dict[str, list[Any]] | None = None,
) -> MagicMock:
    tasks = tasks or []
    missions = missions or []
    mission_tasks = mission_tasks or {}

    async def _list_tasks(
        org_id: str,
        *,
        status: str | None = None,
        mission_id: str | None = None,
        limit: int = 100,
        offset: int = 0,
        **_kw: Any,
    ) -> list[Any]:
        if mission_id is not None:
            rows = mission_tasks.get(mission_id, [])
        else:
            rows = [t for t in tasks if status is None or t.status == status]
        return rows[offset : offset + limit]

    async def _list_missions(
        org_id: str,
        *,
        status: str | None = None,
        priority: str | None = None,
        limit: int = 50,
        offset: int = 0,
        **_kw: Any,
    ) -> list[Any]:
        rows = [
            m
            for m in missions
            if (status is None or m.status == status)
            and (priority is None or m.priority == priority)
        ]
        return rows[offset : offset + limit]

    svc = MagicMock()
    svc.list_departments = AsyncMock(return_value=depts or [])
    svc.list_teams = AsyncMock(return_value=teams or [])
    svc.list_tasks = AsyncMock(side_effect=_list_tasks)
    svc.list_missions = AsyncMock(side_effect=_list_missions)
    svc.list_capabilities = AsyncMock(return_value=capabilities or [])
    return svc


# ── basics ────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_digital_twin_sync_accepts_event() -> None:
    from app.org.digital_twin import OrgDigitalTwin

    twin = OrgDigitalTwin()
    await twin.sync({"event_type": "org.mission.created", "org_id": "org1", "payload": {}})
    assert twin._synced_event_counts["org1"] == 1


# ── capacity / department utilisation ─────────────────────────────────────────


@pytest.mark.asyncio
async def test_capacity_utilisation_is_busy_staffed_agents_over_staffed_agents() -> None:
    from app.org.digital_twin import OrgDigitalTwin

    svc = _fake_service(
        depts=[_dept(ENG_ID, "Engineering"), _dept(OPS_ID, "Operations")],
        teams=[
            _team(TEAM_ENG, ENG_ID, ["a1", "a2", "a3"], manager="lead-eng"),
            _team(TEAM_OPS, OPS_ID, ["o1", "o2"]),
        ],
        tasks=[
            _task("running", TEAM_ENG, agents=["a1"]),
            _task("review", TEAM_ENG, owner="a2"),
            _task("blocked", TEAM_ENG, agents=["a3"]),
            _task("queued", TEAM_ENG, agents=["lead-eng"]),  # queued work ≠ busy
            _task("completed", TEAM_OPS, agents=["o1"]),  # terminal ≠ busy
        ],
    )

    plan = await OrgDigitalTwin().capacity_plan(ORG_ID, svc)

    by_name = {d.name: d for d in plan.departments}
    eng = by_name["Engineering"]
    assert eng.agent_count == 4
    assert eng.busy_agent_count == 3
    assert eng.utilisation == pytest.approx(0.75)
    assert eng.active_task_count == 3
    assert eng.queued_task_count == 1
    assert eng.reason is None

    ops = by_name["Operations"]
    assert ops.agent_count == 2
    assert ops.busy_agent_count == 0
    assert ops.utilisation == 0.0
    assert plan.current_utilisation == {"Engineering": pytest.approx(0.75), "Operations": 0.0}
    assert plan.underutilised == ["Operations"]
    assert plan.overloaded == []


@pytest.mark.asyncio
async def test_capacity_unstaffed_department_is_null_with_reason_not_a_number() -> None:
    from app.org.digital_twin import OrgDigitalTwin

    svc = _fake_service(depts=[_dept(LEGAL_ID, "Legal")], teams=[], tasks=[])

    plan = await OrgDigitalTwin().capacity_plan(ORG_ID, svc)

    (legal,) = plan.departments
    assert legal.utilisation is None
    assert legal.reason and "no agents" in legal.reason.lower()
    assert plan.current_utilisation == {"Legal": None}
    # A null is never classified as over/under-utilised or recommended on.
    assert plan.overloaded == [] and plan.underutilised == []
    assert plan.recommendations == []


@pytest.mark.asyncio
async def test_capacity_does_not_depend_on_department_name() -> None:
    """Regression: utilisation used to be ``0.4 + hash(name) % 60 / 100``."""
    from app.org.digital_twin import OrgDigitalTwin

    def _svc(name: str) -> MagicMock:
        return _fake_service(
            depts=[_dept(ENG_ID, name)],
            teams=[_team(TEAM_ENG, ENG_ID, ["a1", "a2"])],
            tasks=[_task("running", TEAM_ENG, agents=["a1"])],
        )

    values = {
        name: (await OrgDigitalTwin().capacity_plan(ORG_ID, _svc(name))).departments[0].utilisation
        for name in ("Engineering", "Finance", "Legal", "zzz")
    }
    assert set(values.values()) == {0.5}


@pytest.mark.asyncio
async def test_capacity_overloaded_department_gets_recommendation() -> None:
    from app.org.digital_twin import OrgDigitalTwin

    svc = _fake_service(
        depts=[_dept(ENG_ID, "Engineering")],
        teams=[_team(TEAM_ENG, ENG_ID, ["a1"])],
        tasks=[_task("running", TEAM_ENG, agents=["a1"])],
    )
    plan = await OrgDigitalTwin().capacity_plan(ORG_ID, svc)
    assert plan.overloaded == ["Engineering"]
    assert any("Engineering" in r and "100%" in r for r in plan.recommendations)


@pytest.mark.asyncio
async def test_capacity_counts_queued_missions_and_never_invents_clear_time() -> None:
    from app.org.digital_twin import OrgDigitalTwin

    svc = _fake_service(
        missions=[
            SimpleNamespace(status="queued", priority="medium"),
            SimpleNamespace(status="queued", priority="high"),
            SimpleNamespace(status="active", priority="high"),
        ]
    )
    plan = await OrgDigitalTwin().capacity_plan(ORG_ID, svc)
    assert plan.queued_missions == 2
    assert plan.estimated_clear_h is None
    assert plan.estimated_clear_reason


@pytest.mark.asyncio
async def test_capacity_paginates_all_in_flight_tasks() -> None:
    from app.org import digital_twin as dt

    many = [_task("running", TEAM_ENG, agents=[f"a{i}"]) for i in range(dt._PAGE_SIZE + 5)]
    svc = _fake_service(
        depts=[_dept(ENG_ID, "Engineering")],
        teams=[_team(TEAM_ENG, ENG_ID, [f"a{i}" for i in range(dt._PAGE_SIZE + 5)])],
        tasks=many,
    )
    plan = await dt.OrgDigitalTwin().capacity_plan(ORG_ID, svc)
    assert plan.departments[0].active_task_count == dt._PAGE_SIZE + 5
    assert plan.departments[0].utilisation == 1.0


# ── mission simulation ────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_simulate_without_history_returns_nulls_not_priority_lookup() -> None:
    from app.org.digital_twin import OrgDigitalTwin

    svc = _fake_service(teams=[_team(TEAM_ENG, ENG_ID, ["a1"])])
    result = await OrgDigitalTwin().simulate_mission(
        ORG_ID, {"title": "Research competitors", "priority": "high"}, svc
    )
    assert result.estimated_duration_h is None
    assert result.estimated_cost_usd is None
    assert result.confidence is None
    assert result.sample_size == 0
    assert result.estimate_reason and "no completed" in result.estimate_reason.lower()
    # Canned resource numbers are gone: only real staffing counts remain.
    assert result.resource_usage == {
        "staffed_agents": 1,
        "busy_agents": 0,
        "available_agents": 1,
    }
    assert result.feasible is True


@pytest.mark.asyncio
async def test_simulate_estimates_from_completed_missions_of_same_priority() -> None:
    from app.org.digital_twin import OrgDigitalTwin

    t0 = datetime(2026, 1, 1, tzinfo=UTC)
    m1 = SimpleNamespace(
        id=uuid.uuid4(), status="completed", priority="high",
        started_at=t0, completed_at=t0 + timedelta(hours=2),
    )
    m2 = SimpleNamespace(
        id=uuid.uuid4(), status="completed", priority="high",
        started_at=t0, completed_at=t0 + timedelta(hours=4),
    )
    m3 = SimpleNamespace(
        id=uuid.uuid4(), status="completed", priority="high",
        started_at=t0, completed_at=t0 + timedelta(hours=9),
    )
    other = SimpleNamespace(
        id=uuid.uuid4(), status="completed", priority="low",
        started_at=t0, completed_at=t0 + timedelta(hours=100),
    )
    svc = _fake_service(
        teams=[_team(TEAM_ENG, ENG_ID, ["a1"])],
        missions=[m1, m2, m3, other],
        mission_tasks={
            str(m1.id): [_task("completed", None, actual_cost_usd=1.0),
                         _task("completed", None, actual_cost_usd=2.0)],
            str(m2.id): [_task("completed", None, actual_cost_usd=5.0)],
            str(m3.id): [_task("completed", None, actual_cost_usd=None)],
        },
    )
    result = await OrgDigitalTwin().simulate_mission(
        ORG_ID, {"title": "Ship", "priority": "high"}, svc
    )
    assert result.sample_size == 3
    assert result.estimated_duration_h == pytest.approx(4.0)  # median of 2, 4, 9
    assert result.estimated_cost_usd == pytest.approx(4.0)  # median of 3.0, 5.0
    assert result.confidence is None


@pytest.mark.asyncio
async def test_simulate_missing_capability_and_no_agents_is_infeasible() -> None:
    from app.org.digital_twin import OrgDigitalTwin

    svc = _fake_service(capabilities=[SimpleNamespace(name="research")])
    result = await OrgDigitalTwin().simulate_mission(
        ORG_ID,
        {"title": "Audit", "priority": "medium", "required_capabilities": ["research", "audit"]},
        svc,
    )
    assert result.feasible is False
    assert any("audit" in b for b in result.bottlenecks)
    assert any("no agents" in b.lower() for b in result.bottlenecks)
    assert not any("research" in b for b in result.bottlenecks)


@pytest.mark.asyncio
async def test_simulate_without_data_source_is_all_null() -> None:
    from app.org.digital_twin import OrgDigitalTwin

    twin = OrgDigitalTwin()
    result = await twin.simulate_mission(org_id="org1", mission_config={"goal": "Test"})
    assert result.estimated_duration_h is None
    assert result.estimated_cost_usd is None
    assert result.feasible is None
    assert result.estimate_reason
    assert twin._db is None  # read-only: nothing attached, nothing written


# ── what-if ───────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_what_if_is_not_implemented_rather_than_canned() -> None:
    from app.org.digital_twin import OrgDigitalTwin, WhatIfNotSupportedError

    with pytest.raises(WhatIfNotSupportedError):
        await OrgDigitalTwin().what_if(org_id="org1", scenario={"add_agents": 3})
