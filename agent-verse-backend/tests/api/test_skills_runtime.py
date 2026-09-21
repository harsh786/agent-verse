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
    app = _make_app(tenant_id=tenant_id)
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


def test_create_tenant_skill_db_failure_is_swallowed() -> None:
    """_db_save_skill fails silently (logs a warning) — the API call still succeeds
    because the in-memory store is the source of truth."""
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

    assert resp.status_code == 200
    assert resp.json()["status"] == "created"


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


def test_execute_skill_fallback_without_provider() -> None:
    tenant_id = _uniq("tenant")
    app = _make_app(tenant_id=tenant_id)  # no provider configured
    client = TestClient(app)

    resp = client.post(
        "/skills-runtime/headroom/execute",
        headers=H,
        json={"input_context": "compress this text"},
    )

    assert resp.status_code == 200
    body = resp.json()
    assert body["success"] is True
    assert "LLM provider not configured" in body["output"]


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
    app = _make_app(tenant_id=tenant_id)
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


# ── DB persistence helpers — direct unit tests ──────────────────────────────


async def test_db_save_skill_noop_without_factory() -> None:
    from app.api.skills_runtime import _db_save_skill

    # Should return without raising when db_factory is None.
    await _db_save_skill({"skill_id": "x", "name": "n", "description": "d"}, None)


async def test_db_save_skill_executes_insert() -> None:
    from app.api.skills_runtime import _db_save_skill

    session = AsyncMock()
    session.__aenter__ = AsyncMock(return_value=session)
    session.__aexit__ = AsyncMock(return_value=False)
    session.begin = MagicMock()
    session.begin.return_value.__aenter__ = AsyncMock(return_value=None)
    session.begin.return_value.__aexit__ = AsyncMock(return_value=False)
    session.execute = AsyncMock()
    db_factory = MagicMock(return_value=session)

    skill_dict = {
        "skill_id": str(uuid.uuid4()),
        "tenant_id": "tenant-x",
        "name": "Persisted",
        "description": "d",
        "trigger_hints": ["a"],
        "instructions": "do it",
        "allowed_tools": ["tool"],
        "version": "1.0.0",
        "created_at": "2024-01-01T00:00:00Z",
    }

    await _db_save_skill(skill_dict, db_factory)

    # session.execute is called once for the RLS `system_session` context manager
    # (SET LOCAL) and once for the actual INSERT — assert the INSERT happened by
    # checking for the call carrying the bound INSERT params.
    assert session.execute.await_count >= 1
    insert_calls = [
        call
        for call in session.execute.await_args_list
        if len(call.args) > 1 and isinstance(call.args[1], dict) and "id" in call.args[1]
    ]
    assert len(insert_calls) == 1
    assert insert_calls[0].args[1]["name"] == "Persisted"


async def test_db_save_skill_swallows_exception() -> None:
    from app.api.skills_runtime import _db_save_skill

    db_factory = MagicMock(side_effect=RuntimeError("connection refused"))

    # Must not raise — errors are logged and swallowed (in-memory store is
    # the source of truth).
    await _db_save_skill({"skill_id": "x", "name": "n", "description": "d"}, db_factory)


async def test_load_tenant_skills_from_db_noop_without_factory() -> None:
    from app.api.skills_runtime import _load_tenant_skills_from_db

    # Should return immediately without raising.
    await _load_tenant_skills_from_db("some-tenant-not-loaded", None)


async def test_load_tenant_skills_from_db_populates_cache() -> None:
    from app.api.skills_runtime import _loaded_tenants, _load_tenant_skills_from_db, _tenant_skills

    tenant_id = _uniq("db-load-tenant")
    row_id = uuid.uuid4().hex  # 32-char hex, matching the String(32) DB schema

    row = MagicMock()
    row.id = row_id
    row.tenant_id = tenant_id
    row.name = "Loaded From DB"
    row.description = "desc"
    row.trigger_hints = ["hint"]
    row.instructions = "do it"
    row.allowed_tools = ["tool"]
    row.created_at = None
    row.version = "1.0.0"

    result = MagicMock()
    result.fetchall.return_value = [row]

    session = AsyncMock()
    session.__aenter__ = AsyncMock(return_value=session)
    session.__aexit__ = AsyncMock(return_value=False)
    session.begin = MagicMock()
    session.begin.return_value.__aenter__ = AsyncMock(return_value=None)
    session.begin.return_value.__aexit__ = AsyncMock(return_value=False)
    session.execute = AsyncMock(return_value=result)
    db_factory = MagicMock(return_value=session)

    assert tenant_id not in _loaded_tenants
    await _load_tenant_skills_from_db(tenant_id, db_factory)

    assert tenant_id in _loaded_tenants
    loaded = _tenant_skills.get(tenant_id, [])
    assert any(s["name"] == "Loaded From DB" for s in loaded)

    # Idempotent: calling again must not duplicate or re-query.
    session.execute.reset_mock()
    await _load_tenant_skills_from_db(tenant_id, db_factory)
    session.execute.assert_not_called()


async def test_load_tenant_skills_from_db_swallows_exception_and_marks_loaded() -> None:
    from app.api.skills_runtime import _loaded_tenants, _load_tenant_skills_from_db

    tenant_id = _uniq("db-load-fail-tenant")
    db_factory = MagicMock(side_effect=RuntimeError("db down"))

    await _load_tenant_skills_from_db(tenant_id, db_factory)

    # Marked as loaded even on failure, so we don't retry every request.
    assert tenant_id in _loaded_tenants
