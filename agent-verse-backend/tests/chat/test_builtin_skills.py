"""Phase 5 — built-in chat skills over real services."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

from app.chat.skills.builtin import (
    build_list_connected_services_skill,
    build_list_schedules_skill,
    build_submit_goal_skill,
    register_builtin_skills,
)
from app.chat.skills.registry import SkillRegistry


class _FakeServicesAPI:
    def __init__(self, services: list[Any]) -> None:
        self._services = services
        self.calls: list[str] = []

    def list_services(self, tenant_id: str) -> list[Any]:
        self.calls.append(tenant_id)
        return self._services


async def test_list_connected_services_skill_maps_service_objects() -> None:
    api = _FakeServicesAPI([
        SimpleNamespace(id="svc1", name="Slack", status="connected"),
        SimpleNamespace(id="svc2", name="Jira", status="pending"),
    ])
    skill = build_list_connected_services_skill(api)
    out = await skill.handler(tenant_id="t1")
    assert api.calls == ["t1"]
    assert out == [
        {"id": "svc1", "name": "Slack", "status": "connected"},
        {"id": "svc2", "name": "Jira", "status": "pending"},
    ]
    assert skill.scope == "connectors:read"


async def test_register_builtin_skills_wires_available_services() -> None:
    reg = SkillRegistry()
    register_builtin_skills(reg, services_api=_FakeServicesAPI([]))
    assert reg.get("list_connected_services") is not None
    result = await reg.dispatch("list_connected_services", tenant_id="t1")
    assert result == []


def test_register_builtin_skills_skips_missing_services() -> None:
    reg = SkillRegistry()
    register_builtin_skills(reg)  # no services_api
    assert reg.list() == []


class _FakeGoalService:
    def __init__(self) -> None:
        self.submitted: dict[str, Any] = {}

    async def submit_goal(self, **kwargs: Any) -> dict[str, Any]:
        self.submitted = kwargs
        return {"goal_id": "g-1", "status": "accepted"}


class _FakeScheduleStore:
    def list_all(self, *, tenant_ctx: Any) -> list[dict[str, Any]]:
        return [{"id": "sch-1", "cron": "0 9 * * 1"}]


async def test_submit_goal_skill_calls_goal_service() -> None:
    gs = _FakeGoalService()
    skill = build_submit_goal_skill(gs)
    out = await skill.handler(tenant_ctx=SimpleNamespace(tenant_id="t1"), goal="find bugs")
    assert out["goal_id"] == "g-1"
    assert gs.submitted["goal"] == "find bugs" and gs.submitted["dry_run"] is False
    assert skill.scope == "goals:write"


async def test_list_schedules_skill() -> None:
    skill = build_list_schedules_skill(_FakeScheduleStore())
    out = await skill.handler(tenant_ctx=SimpleNamespace(tenant_id="t1"))
    assert out == [{"id": "sch-1", "cron": "0 9 * * 1"}]


async def test_register_wires_all_available() -> None:
    reg = SkillRegistry()
    register_builtin_skills(
        reg,
        services_api=_FakeServicesAPI([]),
        goal_service=_FakeGoalService(),
        schedule_store=_FakeScheduleStore(),
    )
    names = {s.name for s in reg.list()}
    assert names == {"list_connected_services", "submit_goal", "list_schedules"}
