"""Comprehensive unit coverage for app/org/service.py (OrgService).

OrgService is the central orchestrator for the Org OS feature (missions,
agents-as-employees, dashboards). This module systematically exercises every
public (and the important private) method with a mocked ``AsyncSession`` —
no real DB/Docker required — following the fast-tier conventions already
used across ``tests/org/`` (e.g. ``test_org_router.py``,
``test_mission_decomposition.py``, ``test_composer_llm_wiring.py``):

    session = AsyncMock(); session.execute = AsyncMock(return_value=<result>)
    session.add = MagicMock(); session.flush = AsyncMock()
    svc = OrgService(session=session, tenant_id=<uuid str>)

``session.execute`` results are modeled with a tiny ``_Result`` helper
exposing ``scalar()``/``scalar_one_or_none()``/``scalars().all()``/``all()``
— whichever the calling code needs — so real ORM query-builder code paths
execute without touching a database.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.org.service import (
    MAX_TASK_DEPTH,
    MAX_TASKS_PER_MISSION,
    MISSION_STATUSES,
    OrgService,
    _next_cron_fire,
)

# asyncio_mode = "auto" (pyproject.toml) autodetects async test functions —
# no pytestmark/pytest.mark.asyncio needed (and adding it would emit a
# PytestWarning for the sync tests in this module, which fails the suite
# under filterwarnings = ["error"]).


# ── Test helpers ──────────────────────────────────────────────────────────────


class _Result:
    """Minimal stand-in for a SQLAlchemy ``Result``."""

    def __init__(
        self,
        scalar: Any = None,
        scalars_all: list[Any] | None = None,
        all_rows: list[Any] | None = None,
    ) -> None:
        self._scalar = scalar
        self._scalars_all = list(scalars_all or [])
        self._all_rows = list(all_rows if all_rows is not None else [])

    def scalar_one_or_none(self) -> Any:
        return self._scalar

    def scalar(self) -> Any:
        return self._scalar

    def scalars(self) -> Any:
        m = MagicMock()
        m.all = MagicMock(return_value=list(self._scalars_all))
        return m

    def all(self) -> list[Any]:
        return self._all_rows


class _NestedCM:
    async def __aenter__(self) -> _NestedCM:
        return self

    async def __aexit__(self, *_a: Any) -> bool:
        return False


def _session(*, execute_result: Any = None, execute_side_effect: list[Any] | None = None) -> Any:
    session = AsyncMock()
    if execute_side_effect is not None:
        session.execute = AsyncMock(side_effect=execute_side_effect)
    else:
        session.execute = AsyncMock(return_value=execute_result or _Result())
    session.add = MagicMock()
    session.flush = AsyncMock()
    session.delete = AsyncMock()
    session.begin_nested = MagicMock(return_value=_NestedCM())
    return session


def _svc(**kw: Any) -> OrgService:
    session = kw.pop("session", None) or _session()
    tenant_id = kw.pop("tenant_id", None) or str(uuid.uuid4())
    return OrgService(session=session, tenant_id=tenant_id)


def _uuid() -> str:
    return str(uuid.uuid4())


# ── Module-level helpers ──────────────────────────────────────────────────────


def test_next_cron_fire_basic() -> None:
    after = datetime(2026, 1, 1, tzinfo=UTC)
    nxt = _next_cron_fire("0 9 * * *", "UTC", after)
    assert nxt.tzinfo is not None
    assert nxt > after


def test_next_cron_fire_invalid_timezone_falls_back_to_utc() -> None:
    after = datetime(2026, 1, 1, tzinfo=UTC)
    nxt = _next_cron_fire("0 0 * * *", "Not/AZone", after)
    assert nxt.tzinfo is not None


def test_next_cron_fire_defaults_to_now_when_after_missing() -> None:
    nxt = _next_cron_fire("* * * * *")
    assert nxt > datetime.now(UTC) - timedelta(minutes=5)


# ── Internal helpers: _emit_event / _publish_realtime ────────────────────────


async def test_emit_event_persists_and_publishes() -> None:
    session = _session()
    svc = _svc(session=session)
    org_id = uuid.uuid4()
    ev = await svc._emit_event(
        org_id,
        "mission.created",
        title="t",
        description="d",
        entity_type="mission",
        entity_id="m1",
        severity="warning",
        payload={"k": "v"},
        source="api",
        actor_id="agent-1",
    )
    assert ev.event_type == "mission.created"
    assert ev.title == "t"
    session.add.assert_called_once()
    session.flush.assert_awaited()


async def test_publish_realtime_noop_when_no_redis() -> None:
    svc = _svc()
    # Default singleton publisher has no redis client wired -> early return,
    # no exception.
    await svc._publish_realtime(
        org_id=uuid.uuid4(),
        event_type="mission.created",
        entity_type="mission",
        entity_id="m1",
        payload={},
    )


async def test_publish_realtime_swallows_exception() -> None:
    svc = _svc()
    with patch("app.org.events.get_org_event_publisher", side_effect=RuntimeError("boom")):
        # Must not raise -- best-effort bridge.
        await svc._publish_realtime(
            org_id=uuid.uuid4(),
            event_type="mission.created",
            entity_type="mission",
            entity_id="m1",
            payload={},
        )


async def test_publish_realtime_sets_entity_id_and_publishes_when_redis_wired() -> None:
    svc = _svc()
    fake_redis = AsyncMock()
    fake_publisher = SimpleNamespace(_redis=fake_redis, publish=AsyncMock())
    with patch("app.org.events.get_org_event_publisher", return_value=fake_publisher):
        await svc._publish_realtime(
            org_id=uuid.uuid4(),
            event_type="mission.created",
            entity_type="mission",
            entity_id="m1",
            payload={"a": 1},
        )
    fake_publisher.publish.assert_awaited_once()
    _, kwargs = fake_publisher.publish.await_args
    assert kwargs["payload"]["mission_id"] == "m1"
    assert kwargs["event_type"] == "org.mission.created"


# ── Organization CRUD ─────────────────────────────────────────────────────────


async def test_create_organization_defaults_slug_and_clamps_autonomy() -> None:
    svc = _svc()
    org = await svc.create_organization(name="Acme Corp", autonomy_level=99)
    assert org.slug == "acme-corp"
    assert org.autonomy_level == 5  # clamped to max 5


async def test_create_organization_autonomy_clamped_low() -> None:
    svc = _svc()
    org = await svc.create_organization(name="Acme", autonomy_level=-3)
    assert org.autonomy_level == 0


async def test_create_organization_explicit_slug_kept() -> None:
    svc = _svc()
    org = await svc.create_organization(name="Acme", slug="custom-slug")
    assert org.slug == "custom-slug"


async def test_get_organization_found() -> None:
    fake_org = SimpleNamespace(id=uuid.uuid4())
    session = _session(execute_result=_Result(scalar=fake_org))
    svc = _svc(session=session)
    result = await svc.get_organization(_uuid())
    assert result is fake_org


async def test_get_organization_not_found() -> None:
    session = _session(execute_result=_Result(scalar=None))
    svc = _svc(session=session)
    result = await svc.get_organization(_uuid())
    assert result is None


async def test_list_organizations_with_status_filter() -> None:
    orgs = [SimpleNamespace(id=uuid.uuid4()), SimpleNamespace(id=uuid.uuid4())]
    session = _session(execute_result=_Result(scalars_all=orgs))
    svc = _svc(session=session)
    result = await svc.list_organizations(status="active", limit=10, offset=1)
    assert result == orgs


async def test_update_organization_updates_allowed_fields() -> None:
    fake_org = SimpleNamespace(id=uuid.uuid4(), tenant_id="t", created_at=1, name="Old")
    session = _session(execute_result=_Result(scalar=fake_org))
    svc = _svc(session=session)
    updated = await svc.update_organization(_uuid(), {"name": "New", "id": "ignored"})
    assert updated is not None
    assert updated.name == "New"


async def test_update_organization_not_found() -> None:
    session = _session(execute_result=_Result(scalar=None))
    svc = _svc(session=session)
    result = await svc.update_organization(_uuid(), {"name": "x"})
    assert result is None


async def test_delete_organization_archives() -> None:
    fake_org = SimpleNamespace(id=uuid.uuid4(), status="active")
    session = _session(execute_result=_Result(scalar=fake_org))
    svc = _svc(session=session)
    ok = await svc.delete_organization(_uuid())
    assert ok is True
    assert fake_org.status == "archived"


async def test_delete_organization_not_found() -> None:
    session = _session(execute_result=_Result(scalar=None))
    svc = _svc(session=session)
    ok = await svc.delete_organization(_uuid())
    assert ok is False


# ── Org composer (NL → Organisation) ──────────────────────────────────────────


def test_derive_org_name_empty_description_with_industry() -> None:
    assert OrgService._derive_org_name("", "fintech") == "Fintech Organisation"


def test_derive_org_name_empty_description_no_industry() -> None:
    assert OrgService._derive_org_name("   ", "") == "AI Organisation"


def test_derive_org_name_from_description() -> None:
    name = OrgService._derive_org_name("build a great rocket company, fast", "")
    assert name == "Build A Great Rocket Company"


def test_compose_departments_industry_template_plus_keyword() -> None:
    svc = _svc()
    depts = svc._compose_departments("fintech", "we need strong legal and contract support")
    names = [d[0] for d in depts]
    assert "Risk & Compliance" in names
    assert "Legal" in names  # keyword-added


def test_compose_departments_default_template_no_duplicate_keyword() -> None:
    svc = _svc()
    depts = svc._compose_departments("unknown-industry", "plain objective")
    names = [d[0] for d in depts]
    assert names == ["Operations", "Engineering", "Research", "Growth"]


async def test_compose_departments_llm_returns_empty_on_non_list_json() -> None:
    svc = _svc()
    provider = SimpleNamespace(
        complete=AsyncMock(return_value=SimpleNamespace(content='{"not": "a list"}'))
    )
    out = await svc._compose_departments_llm("desc", "tech", provider)
    assert out == []


async def test_compose_departments_llm_strips_code_fence() -> None:
    svc = _svc()
    body = (
        "```json\n"
        '[{"name": "Ops", "purpose": "run things", "capability_domains": ["ops"]}]\n'
        "```"
    )
    provider = SimpleNamespace(complete=AsyncMock(return_value=SimpleNamespace(content=body)))
    out = await svc._compose_departments_llm("desc", "tech", provider)
    assert out == [("Ops", "run things", ["ops"])]


def _composer_service() -> tuple[OrgService, list[str], list[str]]:
    svc = _svc()
    created_departments: list[str] = []
    created_missions: list[str] = []

    async def _create_org(**kw: Any) -> Any:
        return SimpleNamespace(
            id=uuid.uuid4(), name=kw.get("name", "Org"), autonomy_level=kw.get("autonomy_level", 2)
        )

    async def _create_dept(**kw: Any) -> Any:
        created_departments.append(kw["name"])
        return SimpleNamespace(
            id=uuid.uuid4(),
            name=kw["name"],
            purpose=kw.get("purpose", ""),
            capability_domains=kw.get("capability_domains", []),
        )

    async def _create_mission(**kw: Any) -> Any:
        created_missions.append(kw["title"])
        return SimpleNamespace(id=uuid.uuid4(), title=kw.get("title"), status="draft")

    svc.create_organization = AsyncMock(side_effect=_create_org)  # type: ignore[method-assign]
    svc.create_department = AsyncMock(side_effect=_create_dept)  # type: ignore[method-assign]
    svc.create_mission = AsyncMock(side_effect=_create_mission)  # type: ignore[method-assign]
    return svc, created_departments, created_missions


async def test_compose_from_nl_template_path_creates_missions() -> None:
    svc, depts, missions = _composer_service()
    result = await svc.compose_from_nl(
        description="Run a lean startup",
        goals=["Ship v1", "  ", "Grow revenue"],
        industry="ecommerce",
    )
    assert result["composition_method"] == "template"
    assert result["status"] == "ready"
    assert len(missions) == 2  # blank goal skipped
    assert depts  # departments created


async def test_compose_from_nl_llm_insufficient_departments_falls_back() -> None:
    svc, depts, _missions = _composer_service()
    provider = SimpleNamespace(
        complete=AsyncMock(return_value=SimpleNamespace(content='[{"name": "Solo"}]'))
    )
    result = await svc.compose_from_nl(
        description="Do a thing", industry="healthcare", llm_provider=provider
    )
    assert result["composition_method"] == "template"
    assert "Clinical" in depts


async def test_compose_from_nl_llm_raises_falls_back_to_template() -> None:
    svc, depts, _missions = _composer_service()
    provider = SimpleNamespace(complete=AsyncMock(side_effect=RuntimeError("provider down")))
    result = await svc.compose_from_nl(description="Do a thing", industry="", llm_provider=provider)
    assert result["composition_method"] == "template"
    assert depts


# ── Department CRUD ───────────────────────────────────────────────────────────


async def test_create_department() -> None:
    svc = _svc()
    dept = await svc.create_department(org_id=_uuid(), name="Eng", purpose="build stuff")
    assert dept.name == "Eng"
    assert dept.purpose == "build stuff"


async def test_list_departments() -> None:
    depts = [SimpleNamespace(id=uuid.uuid4())]
    session = _session(execute_result=_Result(scalars_all=depts))
    svc = _svc(session=session)
    result = await svc.list_departments(_uuid())
    assert result == depts


async def test_get_department_found_and_not_found() -> None:
    session = _session(execute_result=_Result(scalar=None))
    svc = _svc(session=session)
    assert await svc.get_department(_uuid()) is None


async def test_update_department_not_found() -> None:
    session = _session(execute_result=_Result(scalar=None))
    svc = _svc(session=session)
    assert await svc.update_department(_uuid(), {"name": "x"}) is None


async def test_update_department_updates_fields() -> None:
    fake_dept = SimpleNamespace(id=uuid.uuid4(), tenant_id="t", created_at=1, name="Old")
    session = _session(execute_result=_Result(scalar=fake_dept))
    svc = _svc(session=session)
    updated = await svc.update_department(_uuid(), {"name": "New"})
    assert updated.name == "New"


# ── Team CRUD ─────────────────────────────────────────────────────────────────


async def test_create_team() -> None:
    svc = _svc()
    team = await svc.create_team(
        org_id=_uuid(), name="Squad A", team_type="mission", member_agent_ids=["a1"]
    )
    assert team.name == "Squad A"
    assert team.member_agent_ids == ["a1"]


async def test_list_teams_with_filters() -> None:
    teams = [SimpleNamespace(id=uuid.uuid4())]
    session = _session(execute_result=_Result(scalars_all=teams))
    svc = _svc(session=session)
    result = await svc.list_teams(_uuid(), dept_id=_uuid(), status="active")
    assert result == teams


async def test_get_team_not_found() -> None:
    session = _session(execute_result=_Result(scalar=None))
    svc = _svc(session=session)
    assert await svc.get_team(_uuid()) is None


async def test_update_team_not_found() -> None:
    session = _session(execute_result=_Result(scalar=None))
    svc = _svc(session=session)
    assert await svc.update_team(_uuid(), {"name": "x"}) is None


async def test_update_team_updates_fields() -> None:
    fake_team = SimpleNamespace(id=uuid.uuid4(), tenant_id="t", created_at=1, name="Old")
    session = _session(execute_result=_Result(scalar=fake_team))
    svc = _svc(session=session)
    updated = await svc.update_team(_uuid(), {"name": "New"})
    assert updated.name == "New"


async def test_get_team_member_profiles_no_team() -> None:
    session = _session(execute_result=_Result(scalar=None))
    svc = _svc(session=session)
    result = await svc.get_team_member_profiles(org_id=_uuid(), team_id=_uuid())
    assert result == []


async def test_get_team_member_profiles_no_members() -> None:
    fake_team = SimpleNamespace(id=uuid.uuid4(), member_agent_ids=[])
    session = _session(execute_result=_Result(scalar=fake_team))
    svc = _svc(session=session)
    result = await svc.get_team_member_profiles(org_id=_uuid(), team_id=_uuid())
    assert result == []


async def test_get_team_member_profiles_with_active_task() -> None:
    fake_team = SimpleNamespace(
        id=uuid.uuid4(),
        member_agent_ids=["agent-1", "agent-2"],
        extra_data={"member_roles": {"agent-1": "Lead"}},
    )
    fake_task = SimpleNamespace(
        assigned_agent_ids=["agent-1"], status="running", title="Do work", updated_at=datetime.now(UTC)
    )
    session = _session(
        execute_side_effect=[_Result(scalar=fake_team), _Result(scalars_all=[fake_task])]
    )
    svc = _svc(session=session)
    profiles = await svc.get_team_member_profiles(org_id=_uuid(), team_id=_uuid())
    assert len(profiles) == 2
    agent1 = next(p for p in profiles if p["id"] == "agent-1")
    assert agent1["role"] == "Lead"
    assert agent1["status"] == "executing"
    assert agent1["current_task"] == "Do work"
    agent2 = next(p for p in profiles if p["id"] == "agent-2")
    assert agent2["status"] == "idle"
    assert agent2["role"] == "Mission specialist"


# ── Capability CRUD ───────────────────────────────────────────────────────────


async def test_create_capability() -> None:
    svc = _svc()
    cap = await svc.create_capability(org_id=_uuid(), name="Coding", domain="engineering")
    assert cap.name == "Coding"


async def test_create_capability_global_no_org() -> None:
    svc = _svc()
    cap = await svc.create_capability(name="Global Skill")
    assert cap.org_id is None


async def test_list_capabilities_with_filters() -> None:
    caps = [SimpleNamespace(id=uuid.uuid4())]
    session = _session(execute_result=_Result(scalars_all=caps))
    svc = _svc(session=session)
    result = await svc.list_capabilities(_uuid(), domain="engineering")
    assert result == caps


async def test_list_capabilities_global() -> None:
    session = _session(execute_result=_Result(scalars_all=[]))
    svc = _svc(session=session)
    result = await svc.list_capabilities()
    assert result == []


# ── Mission CRUD ──────────────────────────────────────────────────────────────


async def test_create_mission_without_explicit_status() -> None:
    svc = _svc()
    mission = await svc.create_mission(org_id=_uuid(), title="M1", priority="high")
    assert mission.title == "M1"
    assert mission.priority == "high"


async def test_create_mission_with_explicit_status() -> None:
    svc = _svc()
    mission = await svc.create_mission(org_id=_uuid(), title="M1", status="proposed")
    assert mission.status == "proposed"


async def test_get_mission_found() -> None:
    fake = SimpleNamespace(id=uuid.uuid4())
    session = _session(execute_result=_Result(scalar=fake))
    svc = _svc(session=session)
    assert await svc.get_mission(_uuid()) is fake


async def test_get_mission_not_found() -> None:
    session = _session(execute_result=_Result(scalar=None))
    svc = _svc(session=session)
    assert await svc.get_mission(_uuid()) is None


async def test_list_missions_all_filters_applied() -> None:
    missions = [SimpleNamespace(id=uuid.uuid4())]
    session = _session(execute_result=_Result(scalars_all=missions))
    svc = _svc(session=session)
    result = await svc.list_missions(
        _uuid(),
        status="active",
        exclude_statuses=["archived"],
        priority="high",
        dept_id=_uuid(),
        source="manual",
        limit=5,
        offset=1,
    )
    assert result == missions


async def test_update_mission_status_invalid_raises() -> None:
    svc = _svc()
    with pytest.raises(ValueError):
        await svc.update_mission_status(_uuid(), "not-a-status")


async def test_update_mission_status_mission_not_found() -> None:
    session = _session(execute_result=_Result(scalar=None))
    svc = _svc(session=session)
    result = await svc.update_mission_status(_uuid(), "active")
    assert result is None


async def test_update_mission_status_active_sets_started_at() -> None:
    fake = SimpleNamespace(
        id=uuid.uuid4(), org_id=uuid.uuid4(), status="planned", title="M", started_at=None,
        completed_at=None,
    )
    session = _session(execute_result=_Result(scalar=fake))
    svc = _svc(session=session)
    result = await svc.update_mission_status(str(fake.id), "active")
    assert result.status == "active"
    assert result.started_at is not None


async def test_update_mission_status_completed_sets_completed_at_and_failed_severity() -> None:
    fake = SimpleNamespace(
        id=uuid.uuid4(), org_id=uuid.uuid4(), status="active", title="M", started_at=datetime.now(UTC),
        completed_at=None,
    )
    session = _session(execute_result=_Result(scalar=fake))
    svc = _svc(session=session)
    result = await svc.update_mission_status(str(fake.id), "failed")
    assert result.status == "failed"
    assert result.completed_at is not None


async def test_update_mission_status_all_valid_values_accepted() -> None:
    for status in MISSION_STATUSES:
        fake = SimpleNamespace(
            id=uuid.uuid4(), org_id=uuid.uuid4(), status="draft", title="M", started_at=None,
            completed_at=None,
        )
        session = _session(execute_result=_Result(scalar=fake))
        svc = _svc(session=session)
        result = await svc.update_mission_status(str(fake.id), status)
        assert result.status == status


async def test_update_mission_excludes_protected_fields() -> None:
    fake = SimpleNamespace(
        id=uuid.uuid4(), tenant_id="t", org_id=uuid.uuid4(), created_at=1, updated_at=1,
        status="draft", started_at=None, completed_at=None, title="Old",
    )
    session = _session(execute_result=_Result(scalar=fake))
    svc = _svc(session=session)
    updated = await svc.update_mission(
        str(fake.id), {"title": "New", "status": "completed", "id": "ignored"}
    )
    assert updated.title == "New"
    assert updated.status == "draft"  # protected, unchanged


async def test_update_mission_not_found() -> None:
    session = _session(execute_result=_Result(scalar=None))
    svc = _svc(session=session)
    assert await svc.update_mission(_uuid(), {"title": "x"}) is None


# ── Mission schedules ─────────────────────────────────────────────────────────


async def test_create_mission_schedule_enabled_with_publish_config() -> None:
    svc = _svc(tenant_id=_uuid())
    sched = await svc.create_mission_schedule(
        org_id=_uuid(),
        title="Nightly digest",
        cron_expression="0 0 * * *",
        publish_config={
            "connector_server_id": "srv1",
            "tool_name": "post",
            "arguments": {"x": 1},
            "approved": True,
        },
    )
    assert sched.enabled is True
    assert sched.next_fire_at is not None
    assert sched.publish_config["approved"] is True


async def test_create_mission_schedule_disabled_no_next_fire_no_publish() -> None:
    svc = _svc(tenant_id=_uuid())
    sched = await svc.create_mission_schedule(
        org_id=_uuid(), title="Paused", cron_expression="0 0 * * *", enabled=False
    )
    assert sched.enabled is False
    assert sched.next_fire_at is None
    assert sched.publish_config is None


async def test_list_mission_schedules() -> None:
    scheds = [SimpleNamespace(id=uuid.uuid4())]
    session = _session(execute_result=_Result(scalars_all=scheds))
    svc = _svc(session=session)
    result = await svc.list_mission_schedules(_uuid())
    assert result == scheds


async def test_set_mission_schedule_enabled_true_and_false() -> None:
    fake = SimpleNamespace(
        id=uuid.uuid4(), cron_expression="0 0 * * *", timezone="UTC", enabled=False,
        next_fire_at=None,
    )
    session = _session(execute_result=_Result(scalar=fake))
    svc = _svc(session=session)
    result = await svc.set_mission_schedule_enabled(_uuid(), str(fake.id), True)
    assert result.enabled is True
    assert result.next_fire_at is not None

    fake2 = SimpleNamespace(
        id=uuid.uuid4(), cron_expression="0 0 * * *", timezone="UTC", enabled=True,
        next_fire_at=datetime.now(UTC),
    )
    session2 = _session(execute_result=_Result(scalar=fake2))
    svc2 = _svc(session=session2)
    result2 = await svc2.set_mission_schedule_enabled(_uuid(), str(fake2.id), False)
    assert result2.enabled is False
    assert result2.next_fire_at is None


async def test_set_mission_schedule_enabled_not_found() -> None:
    session = _session(execute_result=_Result(scalar=None))
    svc = _svc(session=session)
    assert await svc.set_mission_schedule_enabled(_uuid(), _uuid(), True) is None


async def test_delete_mission_schedule_found_and_not_found() -> None:
    fake = SimpleNamespace(id=uuid.uuid4())
    session = _session(execute_result=_Result(scalar=fake))
    svc = _svc(session=session)
    assert await svc.delete_mission_schedule(_uuid(), str(fake.id)) is True

    session2 = _session(execute_result=_Result(scalar=None))
    svc2 = _svc(session=session2)
    assert await svc2.delete_mission_schedule(_uuid(), _uuid()) is False


async def test_get_mission_schedule() -> None:
    fake = SimpleNamespace(id=uuid.uuid4())
    session = _session(execute_result=_Result(scalar=fake))
    svc = _svc(session=session)
    assert await svc.get_mission_schedule(_uuid(), str(fake.id)) is fake


async def test_approve_schedule_publishing_none_when_missing() -> None:
    svc = _svc()
    svc.get_mission_schedule = AsyncMock(return_value=None)  # type: ignore[method-assign]
    assert await svc.approve_schedule_publishing(_uuid(), _uuid()) is None


async def test_approve_schedule_publishing_no_publish_target() -> None:
    svc = _svc()
    fake = SimpleNamespace(publish_config=None)
    svc.get_mission_schedule = AsyncMock(return_value=fake)  # type: ignore[method-assign]
    result = await svc.approve_schedule_publishing(_uuid(), _uuid())
    assert result is fake


async def test_approve_schedule_publishing_success() -> None:
    svc = _svc()
    fake = SimpleNamespace(
        publish_config={"connector_server_id": "s1", "tool_name": "t1", "approved": False}
    )
    svc.get_mission_schedule = AsyncMock(return_value=fake)  # type: ignore[method-assign]
    result = await svc.approve_schedule_publishing(_uuid(), _uuid(), approved=True)
    assert result.publish_config["approved"] is True


async def test_release_pending_publish_missions_releases_matching() -> None:
    m1 = SimpleNamespace(
        id=uuid.uuid4(), extra_data={"publish": {"schedule_id": "s1"}, "publish_pending": "true"}
    )
    session = _session(execute_result=_Result(scalars_all=[m1]))
    svc = _svc(session=session)
    released = await svc.release_pending_publish_missions(_uuid(), "s1")
    assert released == [str(m1.id)]
    assert m1.extra_data["publish"]["approved"] is True
    assert "publish_pending" not in m1.extra_data


async def test_release_pending_publish_missions_skips_already_published() -> None:
    m1 = SimpleNamespace(id=uuid.uuid4(), extra_data={"publish": {"schedule_id": "s1"}, "published": True})
    session = _session(execute_result=_Result(scalars_all=[m1]))
    svc = _svc(session=session)
    released = await svc.release_pending_publish_missions(_uuid(), "s1")
    assert released == []


async def test_release_pending_publish_missions_no_rows() -> None:
    session = _session(execute_result=_Result(scalars_all=[]))
    svc = _svc(session=session)
    released = await svc.release_pending_publish_missions(_uuid(), "s1")
    assert released == []


# ── Task CRUD ─────────────────────────────────────────────────────────────────


async def test_create_task_depth_exceeded_raises() -> None:
    svc = _svc()
    with pytest.raises(ValueError):
        await svc.create_task(org_id=_uuid(), title="T", depth=MAX_TASK_DEPTH + 1)


async def test_create_task_max_per_mission_exceeded_raises() -> None:
    session = _session(execute_result=_Result(scalar=MAX_TASKS_PER_MISSION))
    svc = _svc(session=session)
    with pytest.raises(ValueError):
        await svc.create_task(org_id=_uuid(), title="T", mission_id=_uuid())


async def test_create_task_success_with_mission_id() -> None:
    session = _session(execute_result=_Result(scalar=1))
    svc = _svc(session=session)
    task = await svc.create_task(org_id=_uuid(), title="T", mission_id=_uuid())
    assert task.title == "T"


async def test_create_task_success_without_mission_id() -> None:
    svc = _svc()
    task = await svc.create_task(org_id=_uuid(), title="Standalone")
    assert task.title == "Standalone"


async def test_get_task_found_and_not_found() -> None:
    session = _session(execute_result=_Result(scalar=None))
    svc = _svc(session=session)
    assert await svc.get_task(_uuid()) is None


async def test_list_tasks_with_all_filters() -> None:
    tasks = [SimpleNamespace(id=uuid.uuid4())]
    session = _session(execute_result=_Result(scalars_all=tasks))
    svc = _svc(session=session)
    result = await svc.list_tasks(
        _uuid(),
        mission_id=_uuid(),
        workstream_id=_uuid(),
        status="running",
        priority="high",
        assigned_team_id=_uuid(),
        limit=10,
        offset=2,
    )
    assert result == tasks


async def test_update_task_status_invalid_raises() -> None:
    svc = _svc()
    with pytest.raises(ValueError):
        await svc.update_task_status(_uuid(), "nonsense")


async def test_update_task_status_not_found() -> None:
    session = _session(execute_result=_Result(scalar=None))
    svc = _svc(session=session)
    assert await svc.update_task_status(_uuid(), "running") is None


async def test_update_task_status_running_sets_started_at() -> None:
    fake = SimpleNamespace(
        id=uuid.uuid4(), org_id=uuid.uuid4(), status="assigned", started_at=None,
        completed_at=None, evidence=[], outputs=[], audit_trail=[], title="T",
    )
    session = _session(execute_result=_Result(scalar=fake))
    svc = _svc(session=session)
    result = await svc.update_task_status(str(fake.id), "running")
    assert result.started_at is not None
    assert len(result.audit_trail) == 1


async def test_update_task_status_completed_appends_evidence_outputs_cost() -> None:
    fake = SimpleNamespace(
        id=uuid.uuid4(), org_id=uuid.uuid4(), status="running", started_at=datetime.now(UTC),
        completed_at=None, evidence=[], outputs=[], audit_trail=[], title="T",
        actual_cost_usd=None,
    )
    session = _session(execute_result=_Result(scalar=fake))
    svc = _svc(session=session)
    result = await svc.update_task_status(
        str(fake.id), "completed", evidence=["e1"], outputs=["o1"], actual_cost_usd=4.5
    )
    assert result.completed_at is not None
    assert result.evidence == ["e1"]
    assert result.outputs == ["o1"]
    assert result.actual_cost_usd == 4.5


async def test_update_task_status_failed_emits_warning_severity() -> None:
    fake = SimpleNamespace(
        id=uuid.uuid4(), org_id=uuid.uuid4(), status="running", started_at=datetime.now(UTC),
        completed_at=None, evidence=[], outputs=[], audit_trail=[], title="T",
    )
    session = _session(execute_result=_Result(scalar=fake))
    svc = _svc(session=session)
    captured: dict[str, Any] = {}
    orig = svc._emit_event

    async def _spy(*a: Any, **kw: Any) -> Any:
        captured.update(kw)
        return await orig(*a, **kw)

    svc._emit_event = _spy  # type: ignore[method-assign]
    await svc.update_task_status(str(fake.id), "failed")
    assert captured["severity"] == "warning"


# ── Decision CRUD ─────────────────────────────────────────────────────────────


async def test_record_decision() -> None:
    svc = _svc()
    decision = await svc.record_decision(
        org_id=_uuid(),
        entity_type="task",
        entity_id="t1",
        decision_type="approve",
        metadata={"k": "v"},
    )
    assert decision.decision_type == "approve"
    assert decision.extra_data == {"k": "v"}


async def test_list_decisions_with_filters() -> None:
    decisions = [SimpleNamespace(id=uuid.uuid4())]
    session = _session(execute_result=_Result(scalars_all=decisions))
    svc = _svc(session=session)
    result = await svc.list_decisions(
        _uuid(), entity_type="task", entity_id="t1", approval_status="auto_approved"
    )
    assert result == decisions


# ── Events ────────────────────────────────────────────────────────────────────


async def test_record_collaboration_event_with_explicit_id() -> None:
    svc = _svc()
    explicit_id = uuid.uuid4()
    ev = await svc.record_collaboration_event(
        _uuid(),
        from_agent="agent-1",
        kind="chat",
        message="hello",
        payload={"a": 1},
        event_id=explicit_id,
    )
    assert ev.id == explicit_id
    assert ev.event_type == "org.collaboration.message"


async def test_record_collaboration_event_default_id() -> None:
    svc = _svc()
    ev = await svc.record_collaboration_event(
        uuid.uuid4(), from_agent="agent-1", kind="chat", message="hi", payload={}
    )
    assert ev.actor_id == "agent-1"


async def test_list_events_with_all_filters() -> None:
    events = [SimpleNamespace(id=uuid.uuid4())]
    session = _session(execute_result=_Result(scalars_all=events))
    svc = _svc(session=session)
    result = await svc.list_events(
        _uuid(),
        event_type="mission.created",
        severity="info",
        entity_id="m1",
        since=datetime.now(UTC),
        limit=10,
        offset=0,
    )
    assert result == events


# ── Agent audit trail ─────────────────────────────────────────────────────────


async def test_get_agent_audit_merges_and_sorts_by_time() -> None:
    now = datetime.now(UTC)
    ev = SimpleNamespace(
        id=uuid.uuid4(), event_type="task.assigned", created_at=now - timedelta(minutes=5),
        title="Assigned", description="d", payload={"cost_usd": 1.0, "latency_ms": 20, "mission_id": "m1"},
    )
    dec = SimpleNamespace(
        id=uuid.uuid4(), created_at=now - timedelta(minutes=3), decision_type="approve",
        description="ok", why="because", cost_estimate_usd=2.0,
    )
    started = now - timedelta(minutes=2)
    completed = now - timedelta(minutes=1)
    task = SimpleNamespace(
        id=uuid.uuid4(), started_at=started, completed_at=completed, actual_cost_usd=3.0,
        cost_estimate_usd=1.5, title="Task 1", status="completed", mission_id=uuid.uuid4(),
        created_at=now - timedelta(minutes=6),
    )
    session = _session(
        execute_side_effect=[
            _Result(scalars_all=[ev]),  # list_events (called inside get_agent_audit)
            _Result(scalars_all=[dec]),
            _Result(scalars_all=[task]),
        ]
    )
    svc = _svc(session=session)
    entries = await svc.get_agent_audit(_uuid(), "agent-1", limit=10)
    assert [e["kind"] for e in entries] == ["task", "decision", "event"]
    assert entries[0]["duration_ms"] == 60_000
    assert entries[0]["cost_usd"] == 3.0
    assert isinstance(entries[0]["at"], str)


async def test_get_agent_audit_collaboration_message_kind() -> None:
    now = datetime.now(UTC)
    ev = SimpleNamespace(
        id=uuid.uuid4(), event_type="org.collaboration.message", created_at=now, title="hi",
        description="d", payload={},
    )
    session = _session(
        execute_side_effect=[_Result(scalars_all=[ev]), _Result(scalars_all=[]), _Result(scalars_all=[])]
    )
    svc = _svc(session=session)
    entries = await svc.get_agent_audit(_uuid(), "agent-1")
    assert entries[0]["kind"] == "message"


# ── Mission timeline ──────────────────────────────────────────────────────────


async def test_get_mission_timeline_mission_not_found() -> None:
    session = _session(execute_result=_Result(scalar=None))
    svc = _svc(session=session)
    assert await svc.get_mission_timeline(_uuid(), _uuid()) is None


async def test_get_mission_timeline_org_mismatch() -> None:
    fake_mission = SimpleNamespace(id=uuid.uuid4(), org_id=uuid.uuid4())
    session = _session(execute_result=_Result(scalar=fake_mission))
    svc = _svc(session=session)
    result = await svc.get_mission_timeline(_uuid(), str(fake_mission.id))
    assert result is None


async def test_get_mission_timeline_only_created_phase() -> None:
    org_id = uuid.uuid4()
    now = datetime.now(UTC)
    fake_mission = SimpleNamespace(
        id=uuid.uuid4(), org_id=org_id, created_at=now, created_by="agent-1",
        started_at=None, completed_at=None, status="draft", title="M",
    )
    session = _session(
        execute_side_effect=[
            _Result(scalar=fake_mission),  # get_mission
            _Result(scalars_all=[]),  # list_events -> direct_events
            _Result(scalars_all=[]),  # team_result
        ]
    )
    svc = _svc(session=session)
    result = await svc.get_mission_timeline(str(org_id), str(fake_mission.id))
    assert result is not None
    assert [p["name"] for p in result["phases"]] == ["created"]
    assert result["total_ms"] is None


async def test_get_mission_timeline_full_phase_set() -> None:
    org_id = uuid.uuid4()
    now = datetime.now(UTC)
    mission_id = uuid.uuid4()
    fake_mission = SimpleNamespace(
        id=mission_id, org_id=org_id, created_at=now, created_by="agent-1",
        started_at=now + timedelta(minutes=2), completed_at=now + timedelta(minutes=10),
        status="completed", title="M",
    )
    planned_ev = SimpleNamespace(
        event_type="mission.planned", created_at=now + timedelta(minutes=1), actor_id="agent-1",
    )
    started_ev = SimpleNamespace(
        event_type="mission.started", created_at=now + timedelta(minutes=2), actor_id="agent-1",
    )
    done_ev = SimpleNamespace(
        event_type="mission.completed", created_at=now + timedelta(minutes=10), actor_id="agent-1",
    )
    session = _session(
        execute_side_effect=[
            _Result(scalar=fake_mission),
            _Result(scalars_all=[planned_ev, started_ev, done_ev]),
            _Result(scalars_all=[]),
        ]
    )
    svc = _svc(session=session)
    result = await svc.get_mission_timeline(str(org_id), str(mission_id))
    names = [p["name"] for p in result["phases"]]
    assert names == ["created", "planning", "executing", "done"]
    # total_ms is measured from started_at (not created_at) to completed_at.
    assert result["total_ms"] == 8 * 60_000


async def test_get_mission_timeline_last_non_done_phase_extends_to_completed_at() -> None:
    """Anchors are sorted by timestamp, not lifecycle order. When the mission's
    ``started_at`` lands (unusually) after ``completed_at`` and there is no
    explicit lifecycle event, "executing" sorts after "done" and becomes the
    last anchor -- its ``until`` must still be backfilled from
    ``mission.completed_at`` rather than left open-ended."""
    org_id = uuid.uuid4()
    mission_id = uuid.uuid4()
    now = datetime.now(UTC)
    fake_mission = SimpleNamespace(
        id=mission_id, org_id=org_id, created_at=now, created_by="agent-1",
        started_at=now + timedelta(minutes=10), completed_at=now + timedelta(minutes=2),
        status="completed", title="M",
    )
    session = _session(
        execute_side_effect=[
            _Result(scalar=fake_mission),
            _Result(scalars_all=[]),  # no direct lifecycle events
            _Result(scalars_all=[]),  # no team.formed events
        ]
    )
    svc = _svc(session=session)
    result = await svc.get_mission_timeline(str(org_id), str(mission_id))
    names = [p["name"] for p in result["phases"]]
    assert names == ["created", "done", "executing"]
    executing_phase = result["phases"][-1]
    assert executing_phase["until"] == fake_mission.completed_at.isoformat()


# ── Org health ────────────────────────────────────────────────────────────────


async def test_get_org_health_degraded_on_failed_tasks() -> None:
    session = _session(
        execute_side_effect=[
            _Result(scalar=2),  # active missions
            _Result(all_rows=[("completed", 3), ("failed", 1), ("blocked", 2)]),  # task counts
            _Result(scalar=1),  # active teams
            _Result(all_rows=[("critical", 1), ("info", 5)]),  # event counts
            _Result(scalar=3),  # pending approvals
        ]
    )
    svc = _svc(session=session)
    health = await svc.get_org_health(_uuid())
    assert health["health"] == "degraded"
    assert health["active_missions"] == 2
    assert health["task_counts"]["failed"] == 1
    assert health["items_needing_attention"] == 5


async def test_get_org_health_attention_needed_on_many_pending_approvals() -> None:
    session = _session(
        execute_side_effect=[
            _Result(scalar=0),
            _Result(all_rows=[]),
            _Result(scalar=0),
            _Result(all_rows=[]),
            _Result(scalar=6),
        ]
    )
    svc = _svc(session=session)
    health = await svc.get_org_health(_uuid())
    assert health["health"] == "attention_needed"


async def test_get_org_health_healthy() -> None:
    session = _session(
        execute_side_effect=[
            _Result(scalar=1),
            _Result(all_rows=[("completed", 5)]),
            _Result(scalar=1),
            _Result(all_rows=[]),
            _Result(scalar=0),
        ]
    )
    svc = _svc(session=session)
    health = await svc.get_org_health(_uuid())
    assert health["health"] == "healthy"


# ── Blueprint CRUD ────────────────────────────────────────────────────────────


async def test_list_blueprints_with_domain() -> None:
    bps = [SimpleNamespace(id=uuid.uuid4())]
    session = _session(execute_result=_Result(scalars_all=bps))
    svc = _svc(session=session)
    result = await svc.list_blueprints(domain="tech")
    assert result == bps


async def test_get_blueprint_found() -> None:
    fake = SimpleNamespace(id=uuid.uuid4())
    session = _session(execute_result=_Result(scalar=fake))
    svc = _svc(session=session)
    assert await svc.get_blueprint(_uuid()) is fake


async def test_get_blueprint_by_slug_found() -> None:
    fake = SimpleNamespace(slug="s")
    session = _session(execute_result=_Result(scalar=fake))
    svc = _svc(session=session)
    assert await svc.get_blueprint_by_slug("s") is fake


# ── Workstream CRUD ───────────────────────────────────────────────────────────


async def test_create_workstream() -> None:
    svc = _svc()
    ws = await svc.create_workstream(org_id=_uuid(), mission_id=_uuid(), title="WS1")
    assert ws.title == "WS1"


async def test_list_workstreams() -> None:
    wss = [SimpleNamespace(id=uuid.uuid4())]
    session = _session(execute_result=_Result(scalars_all=wss))
    svc = _svc(session=session)
    result = await svc.list_workstreams(_uuid())
    assert result == wss


# ── Mission decomposition ─────────────────────────────────────────────────────


async def test_decompose_mission_llm_failure_falls_back() -> None:
    svc = _svc()
    provider = SimpleNamespace(complete=AsyncMock(side_effect=RuntimeError("down")))
    subtasks = await svc.decompose_mission(
        objective="Do the thing", team_departments=["eng", "ops"], llm_provider=provider
    )
    assert len(subtasks) == 2


async def test_decompose_mission_llm_insufficient_subtasks_falls_back() -> None:
    svc = _svc()
    provider = SimpleNamespace(
        complete=AsyncMock(return_value=SimpleNamespace(content='[{"title": "Only one"}]'))
    )
    subtasks = await svc.decompose_mission(
        objective="Do the thing", team_departments=["eng"], llm_provider=provider
    )
    assert len(subtasks) >= 2  # falls back to phase template


async def test_decompose_mission_empty_objective_uses_default_text() -> None:
    svc = _svc()
    subtasks = await svc.decompose_mission(objective="   ", title="   ")
    assert "mission objective" in subtasks[0]["objective"]


async def test_decompose_mission_llm_non_list_json_returns_empty() -> None:
    svc = _svc()
    provider = SimpleNamespace(
        complete=AsyncMock(return_value=SimpleNamespace(content='{"a": 1}'))
    )
    out = await svc._decompose_mission_llm("obj", provider, 6)
    assert out == []


async def test_decompose_mission_llm_strips_code_fence_directly() -> None:
    svc = _svc()
    body = '```\n[{"title": "Step 1", "objective": "do step 1"}]\n```'
    provider = SimpleNamespace(complete=AsyncMock(return_value=SimpleNamespace(content=body)))
    out = await svc._decompose_mission_llm("obj", provider, 6)
    assert out == [
        {"title": "Step 1", "objective": "do step 1", "department": None, "capabilities": []}
    ]


# ── Task reassignment ─────────────────────────────────────────────────────────


async def test_reassign_task_not_found() -> None:
    session = _session(execute_result=_Result(scalar=None))
    svc = _svc(session=session)
    assert await svc.reassign_task(task_id=_uuid()) is None


async def test_reassign_task_to_team_and_agent() -> None:
    fake_task = SimpleNamespace(
        id=uuid.uuid4(), org_id=uuid.uuid4(), assigned_team_id=None, assigned_agent_ids=[],
        title="T",
    )
    session = _session(execute_result=_Result(scalar=fake_task))
    svc = _svc(session=session)
    new_team = _uuid()
    result = await svc.reassign_task(
        task_id=str(fake_task.id), to_team_id=new_team, to_agent_id="agent-9", reason="rebalance"
    )
    assert str(result.assigned_team_id) == new_team
    assert result.assigned_agent_ids == ["agent-9"]


# ── Team disbanding ───────────────────────────────────────────────────────────


async def test_disband_team_if_idle_no_team_id_noop() -> None:
    svc = _svc()
    await svc._disband_team_if_idle(None, uuid.uuid4())  # no exception, no execute needed


async def test_disband_team_if_idle_team_missing() -> None:
    session = _session(execute_result=_Result(scalar=None))
    svc = _svc(session=session)
    await svc._disband_team_if_idle(_uuid(), uuid.uuid4())


async def test_disband_team_if_idle_already_disbanded_status() -> None:
    fake_team = SimpleNamespace(id=uuid.uuid4(), status="disbanded")
    session = _session(execute_result=_Result(scalar=fake_team))
    svc = _svc(session=session)
    await svc._disband_team_if_idle(str(fake_team.id), uuid.uuid4())
    assert fake_team.status == "disbanded"


async def test_disband_team_if_idle_still_in_use() -> None:
    fake_team = SimpleNamespace(id=uuid.uuid4(), status="active")
    session = _session(execute_side_effect=[_Result(scalar=fake_team), _Result(scalar=1)])
    svc = _svc(session=session)
    await svc._disband_team_if_idle(str(fake_team.id), uuid.uuid4())
    assert fake_team.status == "active"  # untouched


async def test_disband_team_if_idle_disbands_when_unused() -> None:
    fake_team = SimpleNamespace(id=uuid.uuid4(), status="active", name="Squad", updated_at=None)
    session = _session(execute_side_effect=[_Result(scalar=fake_team), _Result(scalar=0)])
    svc = _svc(session=session)
    await svc._disband_team_if_idle(str(fake_team.id), uuid.uuid4())
    assert fake_team.status == "disbanded"


# ── dispatch_mission_goal ─────────────────────────────────────────────────────


async def test_dispatch_mission_goal_mission_not_found() -> None:
    session = _session(execute_result=_Result(scalar=None))
    svc = _svc(session=session)
    result = await svc.dispatch_mission_goal(_uuid())
    assert result == {"error": "mission_not_found", "dispatched": False}


async def test_dispatch_mission_goal_already_dispatched() -> None:
    fake = SimpleNamespace(id=uuid.uuid4(), extra_data={"goal_id": "g0"})
    session = _session(execute_result=_Result(scalar=fake))
    svc = _svc(session=session)
    result = await svc.dispatch_mission_goal(str(fake.id))
    assert result["already_dispatched"] is True
    assert result["goal_id"] == "g0"


async def test_dispatch_mission_goal_no_goal_service() -> None:
    fake = SimpleNamespace(id=uuid.uuid4(), extra_data={})
    session = _session(execute_result=_Result(scalar=fake))
    svc = _svc(session=session)
    result = await svc.dispatch_mission_goal(str(fake.id), app_state=SimpleNamespace())
    assert result == {"error": "goal_service_unavailable", "dispatched": False}


async def test_dispatch_mission_goal_success() -> None:
    fake = SimpleNamespace(
        id=uuid.uuid4(), org_id=uuid.uuid4(), extra_data={}, objective="Do it", title="M",
        priority="high", status="review", started_at=None, completed_at=None,
    )
    session = _session(execute_result=_Result(scalar=fake))
    svc = _svc(session=session)
    goal_service = AsyncMock(submit_goal=AsyncMock(return_value={"goal_id": "g1"}))
    app_state = SimpleNamespace(goal_service=goal_service)
    result = await svc.dispatch_mission_goal(str(fake.id), app_state=app_state)
    assert result == {"goal_id": "g1", "dispatched": True}
    assert fake.extra_data["goal_id"] == "g1"


async def test_dispatch_mission_goal_submit_failure() -> None:
    fake = SimpleNamespace(
        id=uuid.uuid4(), org_id=uuid.uuid4(), extra_data={}, objective="X", title="M",
        priority="low",
    )
    session = _session(execute_result=_Result(scalar=fake))
    svc = _svc(session=session)
    goal_service = AsyncMock(submit_goal=AsyncMock(side_effect=RuntimeError("celery down")))
    app_state = SimpleNamespace(goal_service=goal_service)
    result = await svc.dispatch_mission_goal(str(fake.id), app_state=app_state)
    assert result["dispatched"] is False
    assert "error" in result


# ── finalize_mission ──────────────────────────────────────────────────────────


async def test_finalize_mission_not_found() -> None:
    session = _session(execute_result=_Result(scalar=None))
    svc = _svc(session=session)
    result = await svc.finalize_mission(_uuid())
    assert result == {"error": "mission_not_found", "finalized": False}


async def test_finalize_mission_not_terminal_emits_partial_progress() -> None:
    org_id = uuid.uuid4()
    fake_mission = SimpleNamespace(
        id=uuid.uuid4(), org_id=org_id, extra_data={}, title="M", status="active",
    )
    subtask = SimpleNamespace(status="running", extra_data={"task_kind": "subtask"})
    session = _session(
        execute_side_effect=[_Result(scalar=fake_mission), _Result(scalars_all=[subtask])]
    )
    svc = _svc(session=session)
    result = await svc.finalize_mission(str(fake_mission.id))
    assert result["finalized"] is False
    assert result["goal_status"] is None


async def test_finalize_mission_completed_updates_tasks_and_mission() -> None:
    org_id = uuid.uuid4()
    fake_mission = SimpleNamespace(
        id=uuid.uuid4(), org_id=org_id, extra_data={"goal_id": "g1"}, title="M",
        status="active", objective="Do it", outputs=[], assigned_team_id=None,
        started_at=datetime.now(UTC), completed_at=None,
    )
    fake_task = SimpleNamespace(
        id=uuid.uuid4(), org_id=org_id, status="running", extra_data={"task_kind": "subtask"},
        title="Sub", started_at=datetime.now(UTC), completed_at=None, evidence=[], outputs=[],
        audit_trail=[], actual_cost_usd=None,
    )
    goal_service = AsyncMock(
        get_goal=AsyncMock(return_value={"status": "completed", "result_artifact": {"ok": True}})
    )
    app_state = SimpleNamespace(goal_service=goal_service)
    session = _session(
        execute_side_effect=[
            _Result(scalar=fake_mission),  # get_mission
            _Result(scalars_all=[fake_task]),  # list_tasks
            _Result(scalar=fake_task),  # get_task inside update_task_status
            _Result(scalar=fake_mission),  # get_mission inside update_mission_status
        ]
    )
    svc = _svc(session=session)
    result = await svc.finalize_mission(str(fake_mission.id), app_state=app_state)
    assert result["finalized"] is True
    assert result["status"] == "completed"
    assert fake_task.status == "completed"
    assert fake_mission.extra_data["result"]["deliverable"] == {"ok": True}


async def test_finalize_mission_failed_path() -> None:
    org_id = uuid.uuid4()
    fake_mission = SimpleNamespace(
        id=uuid.uuid4(), org_id=org_id, extra_data={"goal_id": "g1"}, title="M",
        status="active", objective="Do it", outputs=[], assigned_team_id=None,
        started_at=datetime.now(UTC), completed_at=None,
    )
    fake_task = SimpleNamespace(
        id=uuid.uuid4(), org_id=org_id, status="running", extra_data={"task_kind": "subtask"},
        title="Sub", started_at=datetime.now(UTC), completed_at=None, evidence=[], outputs=[],
        audit_trail=[], actual_cost_usd=None,
    )
    goal_service = AsyncMock(get_goal=AsyncMock(return_value={"status": "failed", "result_artifact": None}))
    app_state = SimpleNamespace(goal_service=goal_service)
    session = _session(
        execute_side_effect=[
            _Result(scalar=fake_mission),
            _Result(scalars_all=[fake_task]),
            _Result(scalar=fake_task),
            _Result(scalar=fake_mission),
        ]
    )
    svc = _svc(session=session)
    result = await svc.finalize_mission(str(fake_mission.id), app_state=app_state)
    assert result["status"] == "failed"
    assert fake_task.status == "failed"


async def test_finalize_mission_goal_read_failure_treated_as_not_terminal() -> None:
    org_id = uuid.uuid4()
    fake_mission = SimpleNamespace(
        id=uuid.uuid4(), org_id=org_id, extra_data={"goal_id": "g1"}, title="M", status="active",
    )
    goal_service = AsyncMock(get_goal=AsyncMock(side_effect=RuntimeError("db down")))
    app_state = SimpleNamespace(goal_service=goal_service)
    session = _session(
        execute_side_effect=[_Result(scalar=fake_mission), _Result(scalars_all=[])]
    )
    svc = _svc(session=session)
    result = await svc.finalize_mission(str(fake_mission.id), app_state=app_state)
    assert result["finalized"] is False


# ── create_mission_and_execute ────────────────────────────────────────────────


async def test_create_mission_and_execute_delegates_to_form_team_and_dispatch() -> None:
    svc = _svc()
    fake_mission = SimpleNamespace(id=uuid.uuid4(), title="M", status="draft")
    svc.create_mission = AsyncMock(return_value=fake_mission)  # type: ignore[method-assign]
    svc.update_mission_status = AsyncMock()  # type: ignore[method-assign]
    svc.form_team_and_dispatch = AsyncMock(  # type: ignore[method-assign]
        return_value={"goal_id": "g1", "topology": "sequential"}
    )
    mission, dispatch_result = await svc.create_mission_and_execute(
        org_id=_uuid(), title="M", objective="obj"
    )
    assert mission is fake_mission
    assert dispatch_result["goal_id"] == "g1"
    svc.update_mission_status.assert_awaited_with(str(fake_mission.id), "planned")
    svc.form_team_and_dispatch.assert_awaited_once()


# ── form_team_and_dispatch ────────────────────────────────────────────────────


async def test_form_team_and_dispatch_org_not_found() -> None:
    session = _session(execute_result=_Result(scalar=None))
    svc = _svc(session=session)
    mission = SimpleNamespace(id=uuid.uuid4(), org_id=uuid.uuid4())
    result = await svc.form_team_and_dispatch(mission=mission, org_id=_uuid())
    assert result == {"goal_id": None, "error": "org_not_found"}


async def test_form_team_and_dispatch_orchestration_exception_falls_back(monkeypatch: Any) -> None:
    fake_org = SimpleNamespace(id=uuid.uuid4())
    session = _session(execute_result=_Result(scalar=fake_org))
    svc = _svc(session=session)
    mission = SimpleNamespace(id=uuid.uuid4(), org_id=uuid.uuid4(), title="M")

    class _RaisingOrchestrator:
        def __init__(self, **_kw: Any) -> None:
            raise RuntimeError("orchestrator boom")

    monkeypatch.setattr("app.org.meta_orchestrator.MetaOrchestrator", _RaisingOrchestrator)
    result = await svc.form_team_and_dispatch(
        mission=mission, org_id=str(fake_org.id), objective="Do it", title="M"
    )
    assert result["goal_id"] is None
    assert result.get("warning") == "goal_service_unavailable"


async def test_form_team_and_dispatch_no_team_manifest_no_goal_service(monkeypatch: Any) -> None:
    fake_org = SimpleNamespace(id=uuid.uuid4())
    session = _session(execute_result=_Result(scalar=fake_org))
    svc = _svc(session=session)
    mission = SimpleNamespace(id=uuid.uuid4(), org_id=uuid.uuid4(), title="M")

    orch_plan = SimpleNamespace(
        topology="sequential", departments=["eng"], autonomy_level=2,
        estimated_total_cost_usd=1.0, estimated_total_duration_hours=1.0,
        team_manifest=None, approval_gates=[], model_gateway_profile="p1",
    )

    class _FakeOrchestrator:
        def __init__(self, **_kw: Any) -> None:
            pass

        async def plan_mission(self, *_a: Any, **_kw: Any) -> Any:
            return orch_plan

    monkeypatch.setattr("app.org.meta_orchestrator.MetaOrchestrator", _FakeOrchestrator)
    result = await svc.form_team_and_dispatch(
        mission=mission, org_id=str(fake_org.id), objective="Do it", title="M"
    )
    assert result["topology"] == "sequential"
    assert result.get("warning") == "goal_service_unavailable"


async def test_form_team_and_dispatch_happy_path_forms_team_and_dispatches(
    monkeypatch: Any,
) -> None:
    fake_org = SimpleNamespace(id=uuid.uuid4())
    session = _session(execute_result=_Result(scalar=fake_org))
    svc = _svc(session=session)
    mission = SimpleNamespace(
        id=uuid.uuid4(), org_id=uuid.uuid4(), title="M", assigned_team_id=None, extra_data={},
    )

    role = SimpleNamespace(title="Engineer")
    team_manifest = SimpleNamespace(
        agent_count=2, roles=[role, role], team_name="Alpha Squad",
        formation_reasoning="best fit", requires_human_preview=False,
    )
    orch_plan = SimpleNamespace(
        topology="parallel", departments=["eng", "ops"], autonomy_level=3,
        estimated_total_cost_usd=5.0, estimated_total_duration_hours=2.0,
        team_manifest=team_manifest, approval_gates=[], model_gateway_profile="p1",
    )

    class _FakeOrchestrator:
        def __init__(self, **_kw: Any) -> None:
            pass

        async def plan_mission(self, *_a: Any, **_kw: Any) -> Any:
            return orch_plan

    monkeypatch.setattr("app.org.meta_orchestrator.MetaOrchestrator", _FakeOrchestrator)

    fake_team = SimpleNamespace(id=uuid.uuid4())
    svc.create_team = AsyncMock(return_value=fake_team)  # type: ignore[method-assign]
    svc.decompose_and_assign = AsyncMock(return_value=[])  # type: ignore[method-assign]
    svc.update_mission_status = AsyncMock()  # type: ignore[method-assign]

    goal_service = AsyncMock(submit_goal=AsyncMock(return_value={"goal_id": "g1", "status": "queued"}))
    app_state = SimpleNamespace(goal_service=goal_service)

    result = await svc.form_team_and_dispatch(
        mission=mission,
        org_id=str(fake_org.id),
        objective="Do it",
        title="M",
        dept_id=_uuid(),
        assigned_team_id=None,
        app_state=app_state,
    )
    assert result["goal_id"] == "g1"
    assert result["team_id"] == str(fake_team.id)
    assert str(mission.assigned_team_id) == str(fake_team.id)
    svc.decompose_and_assign.assert_awaited_once()
    svc.update_mission_status.assert_awaited_with(str(mission.id), "active")


async def test_form_team_and_dispatch_decomposition_failure_is_swallowed(monkeypatch: Any) -> None:
    fake_org = SimpleNamespace(id=uuid.uuid4())
    session = _session(execute_result=_Result(scalar=fake_org))
    svc = _svc(session=session)
    mission = SimpleNamespace(
        id=uuid.uuid4(), org_id=uuid.uuid4(), title="M", assigned_team_id=None, extra_data={},
    )
    role = SimpleNamespace(title="Engineer")
    team_manifest = SimpleNamespace(
        agent_count=1, roles=[role], team_name="Solo", formation_reasoning="fit",
        requires_human_preview=False,
    )
    orch_plan = SimpleNamespace(
        topology="sequential", departments=["eng"], autonomy_level=2,
        estimated_total_cost_usd=0.0, estimated_total_duration_hours=0.0,
        team_manifest=team_manifest, approval_gates=[], model_gateway_profile="p1",
    )

    class _FakeOrchestrator:
        def __init__(self, **_kw: Any) -> None:
            pass

        async def plan_mission(self, *_a: Any, **_kw: Any) -> Any:
            return orch_plan

    monkeypatch.setattr("app.org.meta_orchestrator.MetaOrchestrator", _FakeOrchestrator)
    fake_team = SimpleNamespace(id=uuid.uuid4())
    svc.create_team = AsyncMock(return_value=fake_team)  # type: ignore[method-assign]
    svc.decompose_and_assign = AsyncMock(side_effect=RuntimeError("decomp boom"))  # type: ignore[method-assign]
    svc.update_mission_status = AsyncMock()  # type: ignore[method-assign]
    preassigned_team_id = _uuid()
    goal_service = AsyncMock(submit_goal=AsyncMock(return_value={"goal_id": "g7", "status": "queued"}))
    app_state = SimpleNamespace(goal_service=goal_service)
    result = await svc.form_team_and_dispatch(
        mission=mission,
        org_id=str(fake_org.id),
        objective="Do it",
        title="M",
        assigned_team_id=preassigned_team_id,
        app_state=app_state,
    )
    # Decomposition failure must not prevent the topology/team result from
    # being returned -- it is a best-effort side channel.
    assert result["team_id"] == preassigned_team_id
    # A caller-supplied assigned_team_id short-circuits team materialisation,
    # and is also threaded into the execution context for the dispatched goal.
    svc.create_team.assert_not_called()
    goal_service.submit_goal.assert_awaited_once()
    _, submit_kwargs = goal_service.submit_goal.await_args
    assert submit_kwargs["execution_context"]["team_id"] == preassigned_team_id


async def test_form_team_and_dispatch_approval_gate_inner_exceptions_swallowed(
    monkeypatch: Any,
) -> None:
    """Each inner try/except in the approval-gate block (chain check, HITL
    gateway registration, publish, notify) must degrade without aborting the
    dispatch."""
    fake_org = SimpleNamespace(id=uuid.uuid4())
    session = _session(execute_result=_Result(scalar=fake_org))
    svc = _svc(session=session)
    mission = SimpleNamespace(
        id=uuid.uuid4(), org_id=uuid.uuid4(), title="M", assigned_team_id=None, extra_data={},
    )
    orch_plan = SimpleNamespace(
        topology="sequential", departments=[], autonomy_level=2,
        estimated_total_cost_usd=0.0, estimated_total_duration_hours=0.0,
        team_manifest=None, approval_gates=["legal_review"], model_gateway_profile="p1",
    )

    class _FakeOrchestrator:
        def __init__(self, **_kw: Any) -> None:
            pass

        async def plan_mission(self, *_a: Any, **_kw: Any) -> Any:
            return orch_plan

    monkeypatch.setattr("app.org.meta_orchestrator.MetaOrchestrator", _FakeOrchestrator)

    fake_engine = SimpleNamespace(
        check_requires_approval=AsyncMock(side_effect=RuntimeError("chain check boom")),
        create_approval_request=AsyncMock(),
    )
    fake_publisher = SimpleNamespace(publish=AsyncMock(side_effect=RuntimeError("publish boom")))
    monkeypatch.setattr("app.org.approval_chain.get_approval_engine", lambda: fake_engine)
    monkeypatch.setattr("app.org.events.get_org_event_publisher", lambda: fake_publisher)

    fake_task = SimpleNamespace(id=uuid.uuid4())
    svc.create_task = AsyncMock(return_value=fake_task)  # type: ignore[method-assign]
    svc.update_task_status = AsyncMock()  # type: ignore[method-assign]
    svc.update_mission_status = AsyncMock()  # type: ignore[method-assign]

    goal_service = AsyncMock(submit_goal=AsyncMock(return_value={"goal_id": "g9", "status": "queued"}))
    notif = AsyncMock(notify_approval_required=AsyncMock(side_effect=RuntimeError("notify boom")))
    hitl_gateway = MagicMock(request_approval=MagicMock(side_effect=RuntimeError("hitl boom")))
    app_state = SimpleNamespace(
        goal_service=goal_service, notification_service=notif, hitl_gateway=hitl_gateway
    )

    result = await svc.form_team_and_dispatch(
        mission=mission, org_id=str(fake_org.id), objective="Do it", title="M", app_state=app_state
    )
    # Every inner failure is best-effort -- dispatch still succeeds.
    assert result["goal_id"] == "g9"


async def test_form_team_and_dispatch_submit_goal_failure(monkeypatch: Any) -> None:
    fake_org = SimpleNamespace(id=uuid.uuid4())
    session = _session(execute_result=_Result(scalar=fake_org))
    svc = _svc(session=session)
    mission = SimpleNamespace(
        id=uuid.uuid4(), org_id=uuid.uuid4(), title="M", assigned_team_id=None, extra_data={},
    )

    orch_plan = SimpleNamespace(
        topology="sequential", departments=[], autonomy_level=2,
        estimated_total_cost_usd=0.0, estimated_total_duration_hours=0.0,
        team_manifest=None, approval_gates=[], model_gateway_profile="p1",
    )

    class _FakeOrchestrator:
        def __init__(self, **_kw: Any) -> None:
            pass

        async def plan_mission(self, *_a: Any, **_kw: Any) -> Any:
            return orch_plan

    monkeypatch.setattr("app.org.meta_orchestrator.MetaOrchestrator", _FakeOrchestrator)
    goal_service = AsyncMock(submit_goal=AsyncMock(side_effect=RuntimeError("queue down")))
    app_state = SimpleNamespace(goal_service=goal_service)
    result = await svc.form_team_and_dispatch(
        mission=mission, org_id=str(fake_org.id), objective="Do it", title="M", app_state=app_state
    )
    assert "error" in result
    assert result["goal_id"] is None


async def test_form_team_and_dispatch_approval_gates_no_chain(monkeypatch: Any) -> None:
    fake_org = SimpleNamespace(id=uuid.uuid4())
    session = _session(execute_result=_Result(scalar=fake_org))
    svc = _svc(session=session)
    mission = SimpleNamespace(
        id=uuid.uuid4(), org_id=uuid.uuid4(), title="M", assigned_team_id=None, extra_data={},
    )

    orch_plan = SimpleNamespace(
        topology="sequential", departments=[], autonomy_level=2,
        estimated_total_cost_usd=0.0, estimated_total_duration_hours=0.0,
        team_manifest=None, approval_gates=["legal_review"], model_gateway_profile="p1",
    )

    class _FakeOrchestrator:
        def __init__(self, **_kw: Any) -> None:
            pass

        async def plan_mission(self, *_a: Any, **_kw: Any) -> Any:
            return orch_plan

    monkeypatch.setattr("app.org.meta_orchestrator.MetaOrchestrator", _FakeOrchestrator)

    fake_engine = SimpleNamespace(
        check_requires_approval=AsyncMock(return_value=None),
        create_approval_request=AsyncMock(),
    )
    fake_publisher = SimpleNamespace(publish=AsyncMock())
    monkeypatch.setattr("app.org.approval_chain.get_approval_engine", lambda: fake_engine)
    monkeypatch.setattr("app.org.events.get_org_event_publisher", lambda: fake_publisher)

    fake_task = SimpleNamespace(id=uuid.uuid4())
    svc.create_task = AsyncMock(return_value=fake_task)  # type: ignore[method-assign]
    svc.update_task_status = AsyncMock()  # type: ignore[method-assign]
    svc.update_mission_status = AsyncMock()  # type: ignore[method-assign]

    goal_service = AsyncMock(submit_goal=AsyncMock(return_value={"goal_id": "g2", "status": "queued"}))
    notif = AsyncMock(notify_approval_required=AsyncMock())
    hitl_gateway = MagicMock(request_approval=MagicMock(return_value=SimpleNamespace(request_id="hitl1")))
    app_state = SimpleNamespace(
        goal_service=goal_service, notification_service=notif, hitl_gateway=hitl_gateway
    )

    result = await svc.form_team_and_dispatch(
        mission=mission, org_id=str(fake_org.id), objective="Do it", title="M", app_state=app_state
    )
    assert result["goal_id"] == "g2"
    svc.create_task.assert_awaited_once()
    # Last update_task_status call carries the HITL request id in outputs.
    assert svc.update_task_status.await_args.args == (str(fake_task.id), "approval_required")
    notif.notify_approval_required.assert_awaited_once()
    fake_publisher.publish.assert_awaited_once()
    hitl_gateway.request_approval.assert_called_once()


async def test_form_team_and_dispatch_approval_gates_with_chain(monkeypatch: Any) -> None:
    fake_org = SimpleNamespace(id=uuid.uuid4())
    session = _session(execute_result=_Result(scalar=fake_org))
    svc = _svc(session=session)
    mission = SimpleNamespace(
        id=uuid.uuid4(), org_id=uuid.uuid4(), title="M", assigned_team_id=None, extra_data={},
    )

    orch_plan = SimpleNamespace(
        topology="sequential", departments=[], autonomy_level=2,
        estimated_total_cost_usd=0.0, estimated_total_duration_hours=0.0,
        team_manifest=None, approval_gates=[{"type": "finance_review"}], model_gateway_profile="p1",
    )

    class _FakeOrchestrator:
        def __init__(self, **_kw: Any) -> None:
            pass

        async def plan_mission(self, *_a: Any, **_kw: Any) -> Any:
            return orch_plan

    monkeypatch.setattr("app.org.meta_orchestrator.MetaOrchestrator", _FakeOrchestrator)

    fake_chain = SimpleNamespace(risk_threshold="critical", id="chain-1")
    fake_engine = SimpleNamespace(
        check_requires_approval=AsyncMock(return_value=fake_chain),
        create_approval_request=AsyncMock(return_value=SimpleNamespace(request_id="req-1")),
    )
    fake_publisher = SimpleNamespace(publish=AsyncMock())
    monkeypatch.setattr("app.org.approval_chain.get_approval_engine", lambda: fake_engine)
    monkeypatch.setattr("app.org.events.get_org_event_publisher", lambda: fake_publisher)

    fake_task = SimpleNamespace(id=uuid.uuid4())
    svc.create_task = AsyncMock(return_value=fake_task)  # type: ignore[method-assign]
    svc.update_task_status = AsyncMock()  # type: ignore[method-assign]
    svc.update_mission_status = AsyncMock()  # type: ignore[method-assign]

    goal_service = AsyncMock(submit_goal=AsyncMock(return_value={"goal_id": "g3", "status": "queued"}))
    app_state = SimpleNamespace(goal_service=goal_service)

    result = await svc.form_team_and_dispatch(
        mission=mission, org_id=str(fake_org.id), objective="Do it", title="M", app_state=app_state
    )
    assert result["goal_id"] == "g3"
    fake_engine.create_approval_request.assert_awaited_once()


async def test_form_team_and_dispatch_approval_gate_wiring_exception_swallowed(
    monkeypatch: Any,
) -> None:
    fake_org = SimpleNamespace(id=uuid.uuid4())
    session = _session(execute_result=_Result(scalar=fake_org))
    svc = _svc(session=session)
    mission = SimpleNamespace(
        id=uuid.uuid4(), org_id=uuid.uuid4(), title="M", assigned_team_id=None, extra_data={},
    )

    orch_plan = SimpleNamespace(
        topology="sequential", departments=[], autonomy_level=2,
        estimated_total_cost_usd=0.0, estimated_total_duration_hours=0.0,
        team_manifest=None, approval_gates=["x"], model_gateway_profile="p1",
    )

    class _FakeOrchestrator:
        def __init__(self, **_kw: Any) -> None:
            pass

        async def plan_mission(self, *_a: Any, **_kw: Any) -> Any:
            return orch_plan

    monkeypatch.setattr("app.org.meta_orchestrator.MetaOrchestrator", _FakeOrchestrator)
    monkeypatch.setattr(
        "app.org.approval_chain.get_approval_engine",
        MagicMock(side_effect=RuntimeError("engine unavailable")),
    )
    svc.update_mission_status = AsyncMock()  # type: ignore[method-assign]
    goal_service = AsyncMock(submit_goal=AsyncMock(return_value={"goal_id": "g4", "status": "queued"}))
    app_state = SimpleNamespace(goal_service=goal_service)

    result = await svc.form_team_and_dispatch(
        mission=mission, org_id=str(fake_org.id), objective="Do it", title="M", app_state=app_state
    )
    # The gate-wiring failure must not prevent dispatch.
    assert result["goal_id"] == "g4"


async def test_form_team_and_dispatch_no_app_state_goal_service_attr_error() -> None:
    """``getattr(app_state, "goal_service", None)`` path when access itself raises."""

    class _WeirdState:
        @property
        def goal_service(self) -> Any:
            raise RuntimeError("boom")

    fake_org = SimpleNamespace(id=uuid.uuid4())
    session = _session(execute_result=_Result(scalar=fake_org))
    svc = _svc(session=session)
    mission = SimpleNamespace(
        id=uuid.uuid4(), org_id=uuid.uuid4(), title="M", assigned_team_id=None, extra_data={},
    )
    result = await svc.form_team_and_dispatch(
        mission=mission, org_id=str(fake_org.id), objective="Do it", title="M", app_state=_WeirdState()
    )
    assert result.get("warning") == "goal_service_unavailable"


async def test_form_team_and_dispatch_plan_summary_attribute_error_swallowed(
    monkeypatch: Any,
) -> None:
    """A malformed ``orch_plan`` (attribute access raises) while building the
    plan summary must not abort dispatch -- it's wrapped in its own
    try/except."""
    fake_org = SimpleNamespace(id=uuid.uuid4())
    session = _session(execute_result=_Result(scalar=fake_org))
    svc = _svc(session=session)
    mission = SimpleNamespace(
        id=uuid.uuid4(), org_id=uuid.uuid4(), title="M", assigned_team_id=None, extra_data={},
    )

    class _BrokenPlan:
        topology = "sequential"
        departments: list[str] = []
        autonomy_level = 2
        estimated_total_cost_usd = 0.0
        estimated_total_duration_hours = 0.0
        team_manifest = None

        @property
        def approval_gates(self) -> Any:
            raise RuntimeError("plan is malformed")

    orch_plan = _BrokenPlan()

    class _FakeOrchestrator:
        def __init__(self, **_kw: Any) -> None:
            pass

        async def plan_mission(self, *_a: Any, **_kw: Any) -> Any:
            return orch_plan

    monkeypatch.setattr("app.org.meta_orchestrator.MetaOrchestrator", _FakeOrchestrator)
    goal_service = AsyncMock(submit_goal=AsyncMock(return_value={"goal_id": "g5", "status": "queued"}))
    app_state = SimpleNamespace(goal_service=goal_service)
    svc.update_mission_status = AsyncMock()  # type: ignore[method-assign]

    result = await svc.form_team_and_dispatch(
        mission=mission, org_id=str(fake_org.id), objective="Do it", title="M", app_state=app_state
    )
    assert result["goal_id"] == "g5"


async def test_form_team_and_dispatch_create_approval_request_exception_swallowed(
    monkeypatch: Any,
) -> None:
    fake_org = SimpleNamespace(id=uuid.uuid4())
    session = _session(execute_result=_Result(scalar=fake_org))
    svc = _svc(session=session)
    mission = SimpleNamespace(
        id=uuid.uuid4(), org_id=uuid.uuid4(), title="M", assigned_team_id=None, extra_data={},
    )
    orch_plan = SimpleNamespace(
        topology="sequential", departments=[], autonomy_level=2,
        estimated_total_cost_usd=0.0, estimated_total_duration_hours=0.0,
        team_manifest=None, approval_gates=[{"type": "finance_review"}], model_gateway_profile="p1",
    )

    class _FakeOrchestrator:
        def __init__(self, **_kw: Any) -> None:
            pass

        async def plan_mission(self, *_a: Any, **_kw: Any) -> Any:
            return orch_plan

    monkeypatch.setattr("app.org.meta_orchestrator.MetaOrchestrator", _FakeOrchestrator)

    fake_chain = SimpleNamespace(risk_threshold="critical", id="chain-1")
    fake_engine = SimpleNamespace(
        check_requires_approval=AsyncMock(return_value=fake_chain),
        create_approval_request=AsyncMock(side_effect=RuntimeError("approval store down")),
    )
    fake_publisher = SimpleNamespace(publish=AsyncMock())
    monkeypatch.setattr("app.org.approval_chain.get_approval_engine", lambda: fake_engine)
    monkeypatch.setattr("app.org.events.get_org_event_publisher", lambda: fake_publisher)

    fake_task = SimpleNamespace(id=uuid.uuid4())
    svc.create_task = AsyncMock(return_value=fake_task)  # type: ignore[method-assign]
    svc.update_task_status = AsyncMock()  # type: ignore[method-assign]
    svc.update_mission_status = AsyncMock()  # type: ignore[method-assign]

    goal_service = AsyncMock(submit_goal=AsyncMock(return_value={"goal_id": "g6", "status": "queued"}))
    app_state = SimpleNamespace(goal_service=goal_service)

    result = await svc.form_team_and_dispatch(
        mission=mission, org_id=str(fake_org.id), objective="Do it", title="M", app_state=app_state
    )
    assert result["goal_id"] == "g6"
    fake_engine.create_approval_request.assert_awaited_once()
