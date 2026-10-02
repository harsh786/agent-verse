"""Functional tests for app/api/skills_runtime.py — the skill execution engine API.

Covers list/get/create/update/enable/disable/execute/match/versions endpoints,
plus the DB write-through helpers (_db_save_skill / _load_tenant_skills_from_db).

Uses the same TenantMiddleware + FastAPI-app-per-test pattern established in
tests/api/test_civilization_extra4.py, and reuses the module-level in-memory
stores from app.api.skills_runtime (they are process-global, so tests use
unique tenant_ids / skill_ids to avoid cross-test interference).
"""

from __future__ import annotations

import uuid
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.skills_runtime import router as skills_runtime_router
from app.providers.fake import FakeProvider
from app.tenancy.context import PlanTier, TenantContext
from app.tenancy.middleware import SecurityHeadersMiddleware, TenantMiddleware

_VALID_KEY = "av_test_skills_runtime"


def _tenant_ctx(tenant_id: str) -> TenantContext:
    return TenantContext(tenant_id=tenant_id, plan=PlanTier.ENTERPRISE, api_key_id="kid-sr")


def _make_app(
    *,
    tenant_id: str = "tenant-skills-runtime",
    provider: Any = None,
    unauthenticated: bool = False,
) -> FastAPI:
    app = FastAPI()
    ctx = _tenant_ctx(tenant_id)

    async def _resolve(key: str) -> TenantContext | None:
        if unauthenticated:
            return None
        return ctx if key == _VALID_KEY else None

    app.add_middleware(TenantMiddleware, key_resolver=_resolve)
    app.add_middleware(SecurityHeadersMiddleware)
    app.include_router(skills_runtime_router)

    if provider is not None:
        app.state._app_provider = provider

    return app


H = {"X-API-Key": _VALID_KEY}


def _uniq(prefix: str) -> str:
    return f"{prefix}-{uuid.uuid4().hex[:8]}"


# ── Auth guard ──────────────────────────────────────────────────────────────


def test_require_tenant_helper_raises_401_directly() -> None:
    """_require_tenant's own 401 raise is unreachable via HTTP in this test
    setup (TenantMiddleware rejects unauthenticated requests before the route
    handler runs), so exercise the helper directly with a bare mock Request."""
    from fastapi import HTTPException

    from app.api.skills_runtime import _require_tenant

    request = MagicMock()
    request.state = MagicMock(spec=[])  # no `tenant` attribute at all

    with pytest.raises(HTTPException) as exc_info:
        _require_tenant(request)

    assert exc_info.value.status_code == 401


def test_requires_tenant_401() -> None:
    app = _make_app(unauthenticated=True)
    client = TestClient(app)

    resp = client.get("/skills-runtime", headers=H)

    assert resp.status_code == 401


# ── list_skills ───────────────────────────────────────────────────────────


def test_list_skills_includes_platform_builtins() -> None:
    tenant_id = _uniq("tenant")
    app = _make_app(tenant_id=tenant_id)
    client = TestClient(app)

    resp = client.get("/skills-runtime", headers=H)

    assert resp.status_code == 200
    body = resp.json()
    assert body["total"] > 0
    assert any(s["is_platform"] for s in body["skills"])


def test_list_skills_excludes_platform_when_requested() -> None:
    tenant_id = _uniq("tenant")
    app = _make_app(tenant_id=tenant_id)
    client = TestClient(app)

    resp = client.get("/skills-runtime", headers=H, params={"include_platform": False})

    assert resp.status_code == 200
    body = resp.json()
    assert all(not s["is_platform"] for s in body["skills"])


def test_list_skills_status_filter_excludes_active_platform_skills() -> None:
    tenant_id = _uniq("tenant")
    app = _make_app(tenant_id=tenant_id)
    client = TestClient(app)

    resp = client.get("/skills-runtime", headers=H, params={"status": "deprecated"})

    assert resp.status_code == 200
    # All builtins are "active", so filtering by "deprecated" should exclude them.
    body = resp.json()
    assert all(s.get("status") != "active" for s in body["skills"])


def test_list_skills_includes_created_tenant_skill_with_status_filter() -> None:
    tenant_id = _uniq("tenant")
    app = _make_app(tenant_id=tenant_id)
    client = TestClient(app)

    create_resp = client.post(
        "/skills-runtime",
        headers=H,
        json={"name": "Custom Skill", "description": "desc"},
    )
    assert create_resp.status_code == 200

    resp = client.get(
        "/skills-runtime",
        headers=H,
        params={"include_platform": False, "status": "active"},
    )
    body = resp.json()
    assert any(s["name"] == "Custom Skill" for s in body["skills"])


# ── /match (TriggerMatcher) ───────────────────────────────────────────────


def test_match_skills_returns_ranked_matches() -> None:
    tenant_id = _uniq("tenant")
    app = _make_app(tenant_id=tenant_id)
    client = TestClient(app)

    resp = client.get("/skills-runtime/match", headers=H, params={"goal": "create knowledge graph"})

    assert resp.status_code == 200
    body = resp.json()
    assert "matches" in body
    assert any(m["skill_id"] == "graphify" for m in body["matches"])


def test_match_skills_no_match_returns_empty_list() -> None:
    tenant_id = _uniq("tenant")
    app = _make_app(tenant_id=tenant_id)
    client = TestClient(app)

    resp = client.get(
        "/skills-runtime/match", headers=H, params={"goal": "zzz completely unrelated zzz"}
    )

    assert resp.status_code == 200
    assert resp.json()["matches"] == []


# ── /execute (execute_skill_by_id) ────────────────────────────────────────


def test_execute_skill_by_id_not_found() -> None:
    tenant_id = _uniq("tenant")
    app = _make_app(tenant_id=tenant_id)
    client = TestClient(app)

    resp = client.post(
        "/skills-runtime/execute",
        headers=H,
        json={"skill_id": "does-not-exist", "input_context": "hello"},
    )

    assert resp.status_code == 404


def test_execute_skill_by_id_success_with_fake_provider() -> None:
    tenant_id = _uniq("tenant")
    provider = FakeProvider(responses=["done"])
    app = _make_app(tenant_id=tenant_id, provider=provider)
    client = TestClient(app)

    resp = client.post(
        "/skills-runtime/execute",
        headers=H,
        json={"skill_id": "headroom", "input_context": "compress this please"},
    )

    assert resp.status_code == 200
    body = resp.json()
    assert body["success"] is True
    assert body["output"] == "done"
    assert body["skill_id"] == "headroom"


def test_execute_skill_by_id_permission_denied_returns_failed_result() -> None:
    """Disabling a skill for a tenant should make execute() return success=False
    with a PermissionError message, not raise or 403."""
    from app.skills_runtime.executor import permission_checker

    tenant_id = _uniq("tenant")
    permission_checker.disable_for_tenant(tenant_id, "headroom")
    app = _make_app(tenant_id=tenant_id, provider=FakeProvider(responses=["x"]))
    client = TestClient(app)

    try:
        resp = client.post(
            "/skills-runtime/execute",
            headers=H,
            json={"skill_id": "headroom", "input_context": "hi"},
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["success"] is False
        assert "not allowed" in (body["error"] or "")
    finally:
        permission_checker.enable_for_tenant(tenant_id, "headroom")


def test_execute_skill_by_id_finds_tenant_skill() -> None:
    tenant_id = _uniq("tenant")
    app = _make_app(tenant_id=tenant_id, provider=FakeProvider(responses=["ok"]))
    client = TestClient(app)

    create_resp = client.post(
        "/skills-runtime",
        headers=H,
        json={"name": "T Skill", "description": "d", "instructions": "do it"},
    )
    skill_id = create_resp.json()["skill_id"]

    resp = client.post(
        "/skills-runtime/execute",
        headers=H,
        json={"skill_id": skill_id, "input_context": "input"},
    )

    assert resp.status_code == 200
    assert resp.json()["skill_id"] == skill_id


# ── /execute/match ─────────────────────────────────────────────────────────


def test_execute_best_match_no_match() -> None:
    tenant_id = _uniq("tenant")
    app = _make_app(tenant_id=tenant_id)
    client = TestClient(app)

    resp = client.post(
        "/skills-runtime/execute/match",
        headers=H,
        json={"goal": "zzz totally unrelated zzz"},
    )

    assert resp.status_code == 200
    assert resp.json() == {"matched": False}


def test_execute_best_match_executes_top_ranked_skill() -> None:
    tenant_id = _uniq("tenant")
    provider = FakeProvider(responses=["matched output"])
    app = _make_app(tenant_id=tenant_id, provider=provider)
    client = TestClient(app)

    resp = client.post(
        "/skills-runtime/execute/match",
        headers=H,
        json={"goal": "create knowledge graph"},
    )

    assert resp.status_code == 200
    body = resp.json()
    assert body["skill_id"] == "graphify"
    assert body["execution"]["success"] is True
    assert body["execution"]["output"] == "matched output"


# ── permission toggle endpoints ────────────────────────────────────────────


def test_permission_enable_disable_cycle() -> None:
    tenant_id = _uniq("tenant")
    app = _make_app(tenant_id=tenant_id)
    client = TestClient(app)

    disable_resp = client.post(
        "/skills-runtime/permissions/disable", headers=H, json={"skill_id": "headroom"}
    )
    assert disable_resp.status_code == 200
    assert disable_resp.json()["status"] == "disabled"

    enable_resp = client.post(
        "/skills-runtime/permissions/enable", headers=H, json={"skill_id": "headroom"}
    )
    assert enable_resp.status_code == 200
    assert enable_resp.json()["status"] == "enabled"


# ── GET /{skill_id} ─────────────────────────────────────────────────────────


def test_get_skill_platform_found() -> None:
    tenant_id = _uniq("tenant")
    app = _make_app(tenant_id=tenant_id)
    client = TestClient(app)

    resp = client.get("/skills-runtime/headroom", headers=H)

    assert resp.status_code == 200
    assert resp.json()["skill_id"] == "headroom"


def test_get_skill_not_found() -> None:
    tenant_id = _uniq("tenant")
    app = _make_app(tenant_id=tenant_id)
    client = TestClient(app)

    resp = client.get("/skills-runtime/does-not-exist-skill", headers=H)

    assert resp.status_code == 404


def test_get_skill_tenant_skill_found() -> None:
    tenant_id = _uniq("tenant")
    app = _make_app(tenant_id=tenant_id)
    client = TestClient(app)

    create_resp = client.post(
        "/skills-runtime", headers=H, json={"name": "Findable", "description": "d"}
    )
    skill_id = create_resp.json()["skill_id"]

    resp = client.get(f"/skills-runtime/{skill_id}", headers=H)

    assert resp.status_code == 200
    assert resp.json()["name"] == "Findable"


# ── POST "" create_tenant_skill + DB write-through ─────────────────────────


def test_create_tenant_skill_persists_via_db_factory() -> None:
    tenant_id = _uniq("tenant")
    session = AsyncMock()
    session.__aenter__ = AsyncMock(return_value=session)
    session.__aexit__ = AsyncMock(return_value=False)
    session.begin = MagicMock()
    session.begin.return_value.__aenter__ = AsyncMock(return_value=None)
    session.begin.return_value.__aexit__ = AsyncMock(return_value=False)
    session.execute = AsyncMock()

    db_factory = MagicMock(return_value=session)

    app = _make_app(tenant_id=tenant_id)
    app.state.db_session_factory = db_factory
    client = TestClient(app)

    resp = client.post(
        "/skills-runtime",
        headers=H,
        json={
            "name": "DB Persisted Skill",
            "description": "persist me",
            "trigger_hints": ["persist"],
            "instructions": "do the thing",
            "allowed_tools": ["tool_a"],
        },
    )

    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "created"
    assert uuid.UUID(body["skill_id"])


def test_create_tenant_skill_db_failure_is_503_not_created() -> None:
    """OPS-34: a write that did not commit is never reported as created."""
    tenant_id = _uniq("tenant")
    db_factory = MagicMock(side_effect=RuntimeError("db unavailable"))

    app = _make_app(tenant_id=tenant_id)
    app.state.db_session_factory = db_factory
    client = TestClient(app)

    resp = client.post(
        "/skills-runtime",
        headers=H,
        json={"name": "Resilient Skill", "description": "d"},
    )

    assert resp.status_code == 503


# ── enable/disable skill ────────────────────────────────────────────────────


def test_enable_skill_not_found() -> None:
    tenant_id = _uniq("tenant")
    app = _make_app(tenant_id=tenant_id)
    client = TestClient(app)

    resp = client.post("/skills-runtime/does-not-exist/enable", headers=H)

    assert resp.status_code == 404


def test_enable_disable_skill_success() -> None:
    tenant_id = _uniq("tenant")
    app = _make_app(tenant_id=tenant_id)
    client = TestClient(app)

    enable_resp = client.post("/skills-runtime/headroom/enable", headers=H)
    assert enable_resp.status_code == 200
    assert enable_resp.json()["status"] == "enabled"

    list_resp = client.get("/skills-runtime", headers=H)
    headroom = next(s for s in list_resp.json()["skills"] if s["skill_id"] == "headroom")
    assert headroom["enabled"] is True

    disable_resp = client.post("/skills-runtime/headroom/disable", headers=H)
    assert disable_resp.status_code == 200
    assert disable_resp.json()["status"] == "disabled"


# ── POST /{skill_id}/execute (execute_skill) ────────────────────────────────


def test_execute_skill_not_found() -> None:
    tenant_id = _uniq("tenant")
    app = _make_app(tenant_id=tenant_id)
    client = TestClient(app)

    resp = client.post(
        "/skills-runtime/does-not-exist/execute",
        headers=H,
        json={"input_context": "hi"},
    )

    assert resp.status_code == 404


def test_direct_execute_of_disabled_skill_is_403_via_either_disable_api() -> None:
    """OPS-03: POST /{id}/execute never checked the disable state."""
    for disable_call in (
        lambda c: c.post("/skills-runtime/permissions/disable", headers=H,
                         json={"skill_id": "headroom"}),
        lambda c: c.post("/skills-runtime/headroom/disable", headers=H),
    ):
        app = _make_app(tenant_id=_uniq("tenant"), provider=FakeProvider(responses=["x"]))
        client = TestClient(app)
        assert disable_call(client).status_code == 200
        resp = client.post("/skills-runtime/headroom/execute", headers=H,
                           json={"input_context": "hi"})
        assert resp.status_code == 403
        assert "disabled" in resp.json()["detail"]
        assert client.get("/skills-runtime/headroom", headers=H).json()["enabled"] is False


def test_unreadable_skill_state_fails_closed_with_503() -> None:
    class _DownDb:
        def __call__(self) -> Any:
            return self

        async def __aenter__(self) -> Any:
            raise RuntimeError("db down")

        async def __aexit__(self, *a: object) -> None:
            return None

    app = _make_app(tenant_id=_uniq("tenant"), provider=FakeProvider(responses=["x"]))
    app.state.db_session_factory = _DownDb()
    client = TestClient(app)
    resp = client.post("/skills-runtime/headroom/execute", headers=H,
                       json={"input_context": "hi"})
    assert resp.status_code == 503
    by_id = client.post("/skills-runtime/execute", headers=H,
                        json={"skill_id": "headroom", "input_context": "hi"}).json()
    assert by_id["success"] is False


def test_execute_skill_without_provider_is_503_not_canned_success() -> None:
    """Was: success=True with a canned '[X Skill] Processing: ...' output when no
    LLM provider was configured — a fabricated execution."""
    tenant_id = _uniq("tenant")
    app = _make_app(tenant_id=tenant_id)  # no provider configured
    client = TestClient(app)

    resp = client.post(
        "/skills-runtime/headroom/execute",
        headers=H,
        json={"input_context": "compress this text"},
    )

    assert resp.status_code == 503
    history = client.get("/skills-runtime/headroom/executions", headers=H).json()
    assert history["total"] == 0  # no execution recorded for a run that never happened


def test_execute_by_id_and_match_without_provider_are_503_not_canned_success() -> None:
    """OPS-35: SkillExecutor answered success=True with a placeholder output when
    no LLM provider was configured; /execute and /execute/match returned it."""
    client = TestClient(_make_app(tenant_id=_uniq("tenant")))  # no provider

    by_id = client.post(
        "/skills-runtime/execute", headers=H,
        json={"skill_id": "headroom", "input_context": "compress this"},
    )
    assert by_id.status_code == 503
    match = client.post(
        "/skills-runtime/execute/match", headers=H, json={"goal": "create knowledge graph"}
    )
    assert match.status_code == 503


async def test_skill_executor_without_provider_raises_not_success() -> None:
    from app.skills_runtime.executor import SkillExecutor, SkillProviderUnavailableError
    from app.skills_runtime.models import SkillDefinition, SkillScope

    skill = SkillDefinition(
        skill_id="s", name="S", description="d", scope=SkillScope.PLATFORM, instructions="i"
    )
    with pytest.raises(SkillProviderUnavailableError):
        await SkillExecutor().execute(skill=skill, input_context="x", tenant_id="t")


def test_execute_skill_with_provider_success() -> None:
    tenant_id = _uniq("tenant")
    provider = FakeProvider(responses=["LLM said hi"])
    app = _make_app(tenant_id=tenant_id, provider=provider)
    client = TestClient(app)

    resp = client.post(
        "/skills-runtime/headroom/execute",
        headers=H,
        json={"input_context": "compress this text", "goal_id": "goal-1"},
    )

    assert resp.status_code == 200
    body = resp.json()
    assert body["success"] is True
    assert body["output"] == "LLM said hi"


def test_execute_skill_provider_raises_records_error() -> None:
    tenant_id = _uniq("tenant")
    provider = AsyncMock()
    provider.complete = AsyncMock(side_effect=RuntimeError("provider exploded"))
    app = _make_app(tenant_id=tenant_id, provider=provider)
    client = TestClient(app)

    resp = client.post(
        "/skills-runtime/headroom/execute",
        headers=H,
        json={"input_context": "boom"},
    )

    assert resp.status_code == 200
    body = resp.json()
    assert body["success"] is False
    assert body["error"] == "provider exploded"
    assert body["output"] == ""


def test_execute_skill_records_execution_history() -> None:
    tenant_id = _uniq("tenant")
    app = _make_app(tenant_id=tenant_id, provider=FakeProvider(responses=["compressed"]))
    client = TestClient(app)

    exec_resp = client.post(
        "/skills-runtime/headroom/execute",
        headers=H,
        json={"input_context": "compress"},
    )
    execution_id = exec_resp.json()["execution_id"]

    hist_resp = client.get("/skills-runtime/headroom/executions", headers=H)

    assert hist_resp.status_code == 200
    body = hist_resp.json()
    assert body["total"] >= 1
    assert any(e["execution_id"] == execution_id for e in body["executions"])


# ── PUT /{skill_id} (update_tenant_skill) ───────────────────────────────────


def test_update_tenant_skill_not_found() -> None:
    tenant_id = _uniq("tenant")
    app = _make_app(tenant_id=tenant_id)
    client = TestClient(app)

    resp = client.put(
        "/skills-runtime/does-not-exist",
        headers=H,
        json={"name": "New Name"},
    )

    assert resp.status_code == 404


def test_update_tenant_skill_bumps_version_and_archives() -> None:
    tenant_id = _uniq("tenant")
    app = _make_app(tenant_id=tenant_id)
    client = TestClient(app)

    create_resp = client.post(
        "/skills-runtime",
        headers=H,
        json={"name": "Versioned", "description": "d", "version": "1.0.0"},
    )
    skill_id = create_resp.json()["skill_id"]

    update_resp = client.put(
        f"/skills-runtime/{skill_id}",
        headers=H,
        json={"name": "Versioned Updated", "description": "new desc"},
    )

    assert update_resp.status_code == 200
    body = update_resp.json()
    assert body["version"] == "1.0.1"
    assert body["status"] == "updated"

    # Verify the skill fields were actually updated.
    get_resp = client.get(f"/skills-runtime/{skill_id}", headers=H)
    assert get_resp.json()["name"] == "Versioned Updated"

    # Verify the version history was archived.
    versions_resp = client.get(f"/skills-runtime/{skill_id}/versions", headers=H)
    versions_body = versions_resp.json()
    assert versions_body["current_version"] == "1.0.1"
    assert len(versions_body["versions"]) == 1
    assert versions_body["versions"][0]["name"] == "Versioned"


# ── GET /{skill_id}/versions ────────────────────────────────────────────────


def test_get_skill_versions_not_found() -> None:
    tenant_id = _uniq("tenant")
    app = _make_app(tenant_id=tenant_id)
    client = TestClient(app)

    resp = client.get("/skills-runtime/does-not-exist/versions", headers=H)

    assert resp.status_code == 404


def test_get_skill_versions_falls_back_to_platform_skill() -> None:
    tenant_id = _uniq("tenant")
    app = _make_app(tenant_id=tenant_id)
    client = TestClient(app)

    resp = client.get("/skills-runtime/headroom/versions", headers=H)

    assert resp.status_code == 200
    body = resp.json()
    assert body["versions"] == []
    assert body["current_version"] == "1.0.0"


# ── POST /match-trigger ──────────────────────────────────────────────────────


def test_match_trigger_matches_platform_and_tenant_skills() -> None:
    tenant_id = _uniq("tenant")
    app = _make_app(tenant_id=tenant_id)
    client = TestClient(app)

    create_resp = client.post(
        "/skills-runtime",
        headers=H,
        json={
            "name": "Trigger Skill",
            "description": "d",
            "trigger_hints": ["frobnicate widgets"],
        },
    )
    skill_id = create_resp.json()["skill_id"]

    resp = client.post(
        "/skills-runtime/match-trigger",
        headers=H,
        json={"trigger": "please frobnicate widgets now"},
    )

    assert resp.status_code == 200
    body = resp.json()
    matched_ids = {m["skill_id"] for m in body["matches"]}
    assert skill_id in matched_ids
    tenant_match = next(m for m in body["matches"] if m["skill_id"] == skill_id)
    assert tenant_match["enabled"] is True


def test_match_trigger_no_matches() -> None:
    tenant_id = _uniq("tenant")
    app = _make_app(tenant_id=tenant_id)
    client = TestClient(app)

    resp = client.post(
        "/skills-runtime/match-trigger",
        headers=H,
        json={"trigger": "zzz nothing matches this zzz qqq"},
    )

    assert resp.status_code == 200
    assert resp.json()["matches"] == []


# ── Tenant skill store (Postgres source of truth, OPS-34) — direct unit tests ──


def _fake_session(result: Any = None) -> tuple[Any, Any]:
    session = AsyncMock()
    session.__aenter__ = AsyncMock(return_value=session)
    session.__aexit__ = AsyncMock(return_value=False)
    session.begin = MagicMock()
    session.begin.return_value.__aenter__ = AsyncMock(return_value=None)
    session.begin.return_value.__aexit__ = AsyncMock(return_value=False)
    session.execute = AsyncMock(return_value=result)
    return session, MagicMock(return_value=session)


async def test_store_create_runs_insert_under_tenant_rls_not_system_session() -> None:
    """Request path: the write sets the owning tenant's GUC first and never
    switches RLS off; created_at is bound as a datetime (asyncpg)."""
    import datetime as _dt

    from app.skills_runtime.tenant_store import create_tenant_skill

    session, db_factory = _fake_session()
    await create_tenant_skill(
        db_factory,
        {
            "skill_id": str(uuid.uuid4()),
            "tenant_id": "tenant-rls",
            "name": "n",
            "description": "d",
            "created_at": "2024-01-01T00:00:00Z",
        },
    )
    stmts = [str(c.args[0]) for c in session.execute.await_args_list]
    assert not any("row_security" in st for st in stmts), stmts
    first_insert = next(i for i, st in enumerate(stmts) if "INSERT INTO skills" in st)
    first_guc = next(i for i, st in enumerate(stmts) if "set_config('app.tenant_id'" in st)
    assert first_guc < first_insert
    params = session.execute.await_args_list[first_insert].args[1]
    assert params["tenant_id"] == "tenant-rls"
    assert isinstance(params["created_at"], _dt.datetime)


async def test_store_write_failure_raises_instead_of_being_swallowed() -> None:
    from app.skills_runtime.tenant_store import SkillStoreUnavailableError, create_tenant_skill

    with pytest.raises(SkillStoreUnavailableError):
        await create_tenant_skill(
            MagicMock(side_effect=RuntimeError("connection refused")),
            {"skill_id": str(uuid.uuid4()), "tenant_id": "t", "name": "n", "description": "d"},
        )


async def test_store_list_reads_db_on_every_call_with_tenant_predicate() -> None:
    """No once-per-process hydration: each call queries Postgres again, so a
    skill another replica created after this one listed is seen."""
    from app.skills_runtime.tenant_store import list_tenant_skills

    tenant_id = _uniq("db-list")
    row = MagicMock()
    row.id = uuid.uuid4().hex
    row.tenant_id = tenant_id
    row.name = "Loaded From DB"
    row.description = "desc"
    row.trigger_hints = ["hint"]
    row.instructions = "do it"
    row.allowed_tools = ["tool"]
    row.created_at = None
    row.updated_at = None
    row.version = "1.0.0"
    result = MagicMock()
    result.fetchall.return_value = [row]
    session, db_factory = _fake_session(result)

    first = await list_tenant_skills(db_factory, tenant_id)
    second = await list_tenant_skills(db_factory, tenant_id)
    assert [s["name"] for s in first] == ["Loaded From DB"] == [s["name"] for s in second]
    assert str(uuid.UUID(first[0]["skill_id"])) == first[0]["skill_id"]
    selects = [c for c in session.execute.await_args_list if "FROM skills" in str(c.args[0])]
    assert len(selects) == 2
    assert "tenant_id = :tid" in str(selects[0].args[0])
    assert "LIMIT" in str(selects[0].args[0])


async def test_store_read_failure_raises() -> None:
    from app.skills_runtime.tenant_store import SkillStoreUnavailableError, list_tenant_skills

    with pytest.raises(SkillStoreUnavailableError):
        await list_tenant_skills(MagicMock(side_effect=RuntimeError("db down")), "t")


def test_list_is_503_when_the_skill_store_is_unreadable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import app.api.skills_runtime as sr

    async def _no_disabled(*_a: Any, **_k: Any) -> set[str]:
        return set()

    # The disabled-state read succeeds, so the failure is the skill store's.
    monkeypatch.setattr(sr, "disabled_skills", _no_disabled)
    app = _make_app(tenant_id=_uniq("tenant"))
    app.state.db_session_factory = MagicMock(side_effect=RuntimeError("db down"))
    resp = TestClient(app).get("/skills-runtime", headers=H)
    assert resp.status_code == 503


def test_update_is_503_when_the_db_write_fails() -> None:
    app = _make_app(tenant_id=_uniq("tenant"))
    app.state.db_session_factory = MagicMock(side_effect=RuntimeError("db down"))
    resp = TestClient(app).put(
        f"/skills-runtime/{uuid.uuid4()}", headers=H, json={"name": "x"}
    )
    assert resp.status_code == 503
