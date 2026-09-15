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


def test_build_registry_from_app_state_wires_present_services() -> None:
    from app.chat.skills.builtin import build_registry_from_app_state

    app_state = SimpleNamespace(
        goal_service=_FakeGoalService(),
        schedule_store=_FakeScheduleStore(),
        # no services_api on app.state -> that skill is skipped
    )
    reg = build_registry_from_app_state(app_state)
    names = {s.name for s in reg.list()}
    assert names == {"submit_goal", "list_schedules"}


def test_build_registry_from_app_state_empty_is_safe() -> None:
    from app.chat.skills.builtin import build_registry_from_app_state

    reg = build_registry_from_app_state(SimpleNamespace())
    assert reg.list() == []


async def test_generate_document_skill_stores_and_returns_ref() -> None:
    from app.chat.artifact_store import ChatArtifactStore
    from app.chat.skills.builtin import build_generate_document_skill

    store = ChatArtifactStore()
    skill = build_generate_document_skill(store)
    out = await skill.handler(tenant_id="t1", content="Hello report", fmt="pdf", filename="r.pdf")
    assert out["filename"] == "r.pdf" and out["mime"] == "application/pdf"
    assert out["download_url"].endswith("/download")
    stored = store.get(out["artifact_id"], "t1")
    assert stored is not None and stored.content[:4] == b"%PDF"
    assert skill.scope == "documents:write"


async def test_list_pending_approvals_skill() -> None:
    from app.chat.skills.builtin import build_list_pending_approvals_skill

    class _Req:
        def __init__(self, rid: str) -> None:
            self.request_id = rid
            self.goal_id = "g1"
            self.action = "delete prod index"
            self.risk_level = "high"
            self.status = "pending"

    class _Gateway:
        def list_pending(self, *, tenant_ctx: Any, goal_id: Any = None) -> list[Any]:
            return [_Req("req-1")]

    skill = build_list_pending_approvals_skill(_Gateway())
    out = await skill.handler(tenant_ctx=SimpleNamespace(tenant_id="t1"))
    assert out == [
        {"request_id": "req-1", "goal_id": "g1", "action": "delete prod index",
         "risk": "high", "status": "pending"}
    ]
    assert skill.scope == "governance:read"


async def test_resolve_approval_skill_approve_and_reject() -> None:
    from app.chat.skills.builtin import build_resolve_approval_skill

    class _Gateway:
        def __init__(self) -> None:
            self.calls: list[tuple[str, str]] = []

        async def approve(self, request_id: str, *, approver: str, note: str, tenant_ctx: Any) -> bool:
            self.calls.append(("approve", request_id))
            return True

        async def reject(self, request_id: str, *, approver: str, note: str, tenant_ctx: Any) -> bool:
            self.calls.append(("reject", request_id))
            return True

    gw = _Gateway()
    skill = build_resolve_approval_skill(gw)
    ctx = SimpleNamespace(tenant_id="t1")
    a = await skill.handler(tenant_ctx=ctx, request_id="r1", decision="approve")
    r = await skill.handler(tenant_ctx=ctx, request_id="r2", decision="Reject", note="unsafe")
    assert a == {"request_id": "r1", "decision": "approve", "ok": True}
    assert r["decision"] == "reject" and r["ok"] is True
    assert gw.calls == [("approve", "r1"), ("reject", "r2")]
    assert skill.scope == "governance:write"


async def test_resolve_approval_rejects_bad_decision() -> None:
    import pytest

    from app.chat.skills.builtin import build_resolve_approval_skill

    skill = build_resolve_approval_skill(object())
    with pytest.raises(ValueError, match="approve or reject"):
        await skill.handler(tenant_ctx=SimpleNamespace(tenant_id="t"), request_id="r", decision="maybe")
