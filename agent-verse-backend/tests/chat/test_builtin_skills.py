"""Phase 5 — built-in chat skills over real services."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

from app.chat.skills.builtin import build_list_connected_services_skill, register_builtin_skills
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
