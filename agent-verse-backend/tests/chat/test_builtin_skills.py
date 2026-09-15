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
    assert names == {
        "list_connected_services", "connect_service", "submit_goal", "list_schedules"
    }


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


# ── Phase 5 additions: workflows + knowledge-base skills ──────────────────────


class _FakeWorkflowService:
    async def list(self, tenant_id: str) -> list[dict[str, Any]]:
        return [{"id": "wf1", "name": "Weekly Report", "status": "published",
                 "description": "d", "extra": "ignored"}]


class _FakeWorkflowRunner:
    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    async def trigger(self, *, workflow_id: str, tenant_id: str,
                      inputs: dict[str, Any]) -> dict[str, Any]:
        self.calls.append({"workflow_id": workflow_id, "tenant_id": tenant_id, "inputs": inputs})
        return {"run_id": "r1", "workflow_id": workflow_id, "status": "pending"}


class _FakeKnowledgeStore:
    def __init__(self) -> None:
        self.searched: list[Any] = []
        self.ingested: list[Any] = []

    async def search(self, query: str, collection_id: str, *, top_k: int,
                     tenant_ctx: Any) -> list[dict[str, Any]]:
        self.searched.append((query, collection_id, top_k, tenant_ctx))
        return [{"source": "doc1", "score": 0.9, "content": "hit"}]

    async def ingest_document(self, *, collection_id: str, content: str, tenant_ctx: Any,
                              source_url: str = "", source_type: str = "text") -> str:
        self.ingested.append((collection_id, content, source_url, source_type))
        return "doc-123"


async def test_list_workflows_skill_maps_rows() -> None:
    from app.chat.skills.builtin import build_list_workflows_skill

    skill = build_list_workflows_skill(_FakeWorkflowService())
    out = await skill.handler(tenant_id="t1")
    assert out == [{"id": "wf1", "name": "Weekly Report", "status": "published", "description": "d"}]
    assert skill.scope == "workflows:read"


async def test_run_workflow_skill_triggers_runner() -> None:
    from app.chat.skills.builtin import build_run_workflow_skill

    runner = _FakeWorkflowRunner()
    skill = build_run_workflow_skill(runner)
    out = await skill.handler(tenant_id="t1", workflow_id="wf1", inputs={"k": "v"})
    assert out["run_id"] == "r1"
    assert runner.calls == [{"workflow_id": "wf1", "tenant_id": "t1", "inputs": {"k": "v"}}]
    assert skill.scope == "workflows:write"


async def test_search_and_ingest_knowledge_skills() -> None:
    from app.chat.skills.builtin import (
        build_ingest_knowledge_skill,
        build_search_knowledge_skill,
    )

    store = _FakeKnowledgeStore()
    ctx = SimpleNamespace(tenant_id="t1")
    search = build_search_knowledge_skill(store)
    hits = await search.handler(tenant_ctx=ctx, query="q", collection_id="c1", top_k=3)
    assert hits == [{"source": "doc1", "score": 0.9, "content": "hit"}]
    assert store.searched == [("q", "c1", 3, ctx)]
    assert search.scope == "knowledge:read"

    ingest = build_ingest_knowledge_skill(store)
    res = await ingest.handler(tenant_ctx=ctx, collection_id="c1", content="text")
    assert res == {"ingested": True, "collection_id": "c1", "doc_id": "doc-123"}
    assert store.ingested == [("c1", "text", "", "text")]
    assert ingest.scope == "knowledge:write"


async def test_register_wires_workflow_and_knowledge_skills() -> None:
    reg = SkillRegistry()
    register_builtin_skills(
        reg,
        workflow_service=_FakeWorkflowService(),
        workflow_runner=_FakeWorkflowRunner(),
        knowledge_store=_FakeKnowledgeStore(),
    )
    names = {s.name for s in reg.list()}
    assert {"list_workflows", "run_workflow", "search_knowledge", "ingest_knowledge"} <= names


class _FakeChatServiceForModel:
    def __init__(self) -> None:
        self.calls: list[Any] = []

    async def aupdate_session(self, session_id: str, tenant_id: str, **fields: Any) -> Any:
        self.calls.append((session_id, tenant_id, fields))
        return SimpleNamespace(preferred_model=fields.get("preferred_model"))


async def test_set_conversation_model_skill_updates_session() -> None:
    from app.chat.skills.builtin import build_set_conversation_model_skill

    chat = _FakeChatServiceForModel()
    skill = build_set_conversation_model_skill(chat)
    out = await skill.handler(session_id="s1", tenant_id="t1", model="claude-opus-5")
    assert out == {"session_id": "s1", "preferred_model": "claude-opus-5", "ok": True}
    assert chat.calls == [("s1", "t1", {"preferred_model": "claude-opus-5"})]
    assert skill.scope == "models:write"


async def test_launch_org_mission_skill_calls_launcher() -> None:
    from app.chat.skills.builtin import build_launch_org_mission_skill

    calls: list[dict[str, Any]] = []

    async def _launcher(*, tenant_id: str, objective: str, title: Any, org_id: Any) -> dict:
        calls.append({"tenant_id": tenant_id, "objective": objective,
                      "title": title, "org_id": org_id})
        return {"mission_id": "m1", "status": "active", "org_id": "o1"}

    skill = build_launch_org_mission_skill(_launcher)
    out = await skill.handler(tenant_id="t1", objective="draft Q3 plan")
    assert out == {"mission_id": "m1", "status": "active", "org_id": "o1"}
    assert calls == [{"tenant_id": "t1", "objective": "draft Q3 plan",
                      "title": None, "org_id": None}]
    assert skill.scope == "org:write"


async def test_org_mission_status_skill_calls_reader() -> None:
    from app.chat.skills.builtin import build_org_mission_status_skill

    async def _reader(*, tenant_id: str, mission_id: str) -> dict:
        return {"mission_id": mission_id, "status": "completed", "title": "Q3"}

    skill = build_org_mission_status_skill(_reader)
    out = await skill.handler(tenant_id="t1", mission_id="m1")
    assert out["status"] == "completed" and out["title"] == "Q3"
    assert skill.scope == "org:read"


def test_build_org_mission_callables_none_without_factory() -> None:
    from app.chat.skills.builtin import _build_org_mission_callables

    assert _build_org_mission_callables(None) == (None, None)


async def test_connect_service_skill_returns_oauth_url_not_secrets() -> None:
    from app.chat.skills.builtin import build_connect_service_skill

    class _API:
        def initiate_connection(self, tenant_id, name, url, scopes):  # type: ignore[no-untyped-def]
            return {"service_id": "svc1", "oauth_url": "https://x/oauth?id=svc1",
                    "service": object()}

    skill = build_connect_service_skill(_API())
    out = await skill.handler(tenant_id="t1", name="Slack")
    assert out["service_id"] == "svc1"
    assert out["authorization_url"] == "https://x/oauth?id=svc1"
    assert "password" in out["next_step"].lower()  # explicit no-secrets guidance
    assert skill.scope == "connectors:write"
    # No secret/credential field is ever returned.
    assert not any(k in out for k in ("password", "token", "secret", "credential"))
