"""Tests for previously-uncovered /tenants endpoints.

Targets the gaps left by the existing tenants test files: signup rate
limiting, the SSE stream-token mint, the lightweight LLM-config store,
the provider catalog, tenant membership listing/invites with a real DB
mock, notification preferences, sessions, data export, and account
deletion.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.tenants import router as tenants_router
from app.tenancy.context import PlanTier, TenantContext
from app.tenancy.middleware import SecurityHeadersMiddleware, TenantMiddleware

_CTX = TenantContext(
    tenant_id="tid-uncov", plan=PlanTier.PROFESSIONAL, api_key_id="kid-uncov", roles=("admin",)
)
_VALID_KEY = "av_test_uncov"
H = {"X-API-Key": _VALID_KEY}


def _make_app(tenant_service: Any = None) -> FastAPI:
    app = FastAPI()

    async def _resolve(key: str) -> TenantContext | None:
        return _CTX if key == _VALID_KEY else None

    app.add_middleware(TenantMiddleware, key_resolver=_resolve)
    app.add_middleware(SecurityHeadersMiddleware)
    app.include_router(tenants_router)
    app.state.tenant_service = tenant_service or AsyncMock()
    return app


class _FakeRedis:
    """Minimal async redis stand-in with an in-memory counter/store."""

    def __init__(self) -> None:
        self._counts: dict[str, int] = {}
        self._store: dict[str, str] = {}
        self._expired: list[str] = []

    async def incr(self, key: str) -> int:
        self._counts[key] = self._counts.get(key, 0) + 1
        return self._counts[key]

    async def expire(self, key: str, ttl: int) -> None:
        self._expired.append(key)

    async def get(self, key: str) -> str | None:
        return self._store.get(key)

    async def setex(self, key: str, ttl: int, value: str) -> None:
        self._store[key] = value


class _RaisingRedis:
    async def incr(self, key: str) -> int:
        raise ConnectionError("redis down")

    async def expire(self, key: str, ttl: int) -> None:
        raise ConnectionError("redis down")

    async def get(self, key: str) -> str | None:
        raise ConnectionError("redis down")

    async def setex(self, key: str, ttl: int, value: str) -> None:
        raise ConnectionError("redis down")


def _make_db_mock(
    rows: list | None = None,
    scalar_results: list | None = None,
    raise_on_execute: Exception | None = None,
) -> Any:
    """Build a mock db_session_factory whose session.execute can be scripted
    with a distinct scalar_one_or_none() result per call (via side_effect)."""
    session = AsyncMock()

    if raise_on_execute:
        session.execute = AsyncMock(side_effect=raise_on_execute)
    else:
        results = []
        scalars_iter = iter(scalar_results or [])

        def _make_result() -> MagicMock:
            r = MagicMock()
            r.scalars.return_value.all.return_value = rows or []
            r.all.return_value = rows or []
            try:
                r.scalar_one_or_none.return_value = next(scalars_iter)
            except StopIteration:
                r.scalar_one_or_none.return_value = None
            return r

        # session.execute is called an unknown number of times (RLS SET_CONFIG +
        # actual queries); return a fresh scripted result each call.
        session.execute = AsyncMock(side_effect=lambda *a, **k: _make_result())
        results.append(None)  # keep list referenced (avoids lint complaints)

    session.add = MagicMock()
    session.delete = AsyncMock()
    session.flush = AsyncMock()

    begin_cm = AsyncMock()
    begin_cm.__aenter__ = AsyncMock(return_value=None)
    begin_cm.__aexit__ = AsyncMock(return_value=False)
    session.begin = MagicMock(return_value=begin_cm)

    cm = AsyncMock()
    cm.__aenter__ = AsyncMock(return_value=session)
    cm.__aexit__ = AsyncMock(return_value=False)

    db = MagicMock(return_value=cm)
    return db


# ---------------------------------------------------------------------------
# signup — IP rate limiting
# ---------------------------------------------------------------------------


def test_signup_rate_limited_after_ten_attempts() -> None:
    """Line 80-94: 11th signup from the same IP within the window gets 429."""
    svc = AsyncMock()
    svc.create_tenant.return_value = {"tenant_id": "t1", "api_key": "av_free_x", "name": "A"}
    app = _make_app(svc)
    app.state._redis = _FakeRedis()
    client = TestClient(app, raise_server_exceptions=False)

    last_resp = None
    for _ in range(11):
        last_resp = client.post(
            "/tenants/signup", json={"name": "Acme", "email": "acme@example.com"}
        )
    assert last_resp is not None
    assert last_resp.status_code == 429


def test_signup_succeeds_under_rate_limit() -> None:
    """A handful of signups under the threshold still succeed with redis wired."""
    svc = AsyncMock()
    svc.create_tenant.return_value = {"tenant_id": "t1", "api_key": "av_free_x", "name": "A"}
    app = _make_app(svc)
    app.state._redis = _FakeRedis()
    client = TestClient(app, raise_server_exceptions=False)
    resp = client.post("/tenants/signup", json={"name": "Acme", "email": "acme2@example.com"})
    assert resp.status_code == 201


def test_signup_fails_open_when_redis_errors() -> None:
    """Lines 91-94: a Redis error during rate-limiting must not block signup."""
    svc = AsyncMock()
    svc.create_tenant.return_value = {"tenant_id": "t1", "api_key": "av_free_x", "name": "A"}
    app = _make_app(svc)
    app.state._redis = _RaisingRedis()
    client = TestClient(app, raise_server_exceptions=False)
    resp = client.post("/tenants/signup", json={"name": "Acme", "email": "acme3@example.com"})
    assert resp.status_code == 201


# ---------------------------------------------------------------------------
# GET /tenants/stream-token
# ---------------------------------------------------------------------------


def test_get_stream_token_returns_token_and_ttl() -> None:
    """Lines 117-131: minted stream token is tenant-bound and short-lived."""
    client = TestClient(_make_app(), raise_server_exceptions=False)
    resp = client.get("/tenants/stream-token", headers=H)
    assert resp.status_code == 200
    body = resp.json()
    assert body.get("token")
    assert body["expires_in"] == 600


def test_get_stream_token_requires_auth() -> None:
    client = TestClient(_make_app(), raise_server_exceptions=False)
    resp = client.get("/tenants/stream-token")
    assert resp.status_code == 401


# ---------------------------------------------------------------------------
# GET/PUT /tenants/me/llm-config (lightweight store)
# ---------------------------------------------------------------------------


def test_get_llm_config_lightweight_no_tenant_service_attr() -> None:
    """Lines 321-329: app.state has no tenant_service attribute → {}."""
    app = FastAPI()

    async def _resolve(key: str) -> TenantContext | None:
        return _CTX if key == _VALID_KEY else None

    app.add_middleware(TenantMiddleware, key_resolver=_resolve)
    app.add_middleware(SecurityHeadersMiddleware)
    app.include_router(tenants_router)
    # deliberately no app.state.tenant_service
    client = TestClient(app, raise_server_exceptions=False)
    resp = client.get("/tenants/me/llm-config", headers=H)
    assert resp.status_code == 200
    assert resp.json() == {}


def test_get_llm_config_lightweight_returns_stored_config() -> None:
    svc = AsyncMock()
    svc.get_llm_config.return_value = {"provider": "anthropic", "model": "claude-opus"}
    client = TestClient(_make_app(svc), raise_server_exceptions=False)
    resp = client.get("/tenants/me/llm-config", headers=H)
    assert resp.status_code == 200
    assert resp.json()["provider"] == "anthropic"


def test_get_llm_config_lightweight_swallows_service_exception() -> None:
    """Lines 324-328: an exception from the service falls back to {}."""
    svc = AsyncMock()
    svc.get_llm_config.side_effect = RuntimeError("boom")
    client = TestClient(_make_app(svc), raise_server_exceptions=False)
    resp = client.get("/tenants/me/llm-config", headers=H)
    assert resp.status_code == 200
    assert resp.json() == {}


def test_save_llm_config_lightweight_success() -> None:
    svc = AsyncMock()
    svc.save_llm_config.return_value = None
    client = TestClient(_make_app(svc), raise_server_exceptions=False)
    resp = client.put(
        "/tenants/me/llm-config", json={"provider": "openai", "model": "gpt-4o"}, headers=H
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "saved"
    assert body["provider"] == "openai"


def test_save_llm_config_lightweight_no_tenant_service() -> None:
    """Lines 340-347: no tenant_service configured → saved_in_memory fallback."""
    app = FastAPI()

    async def _resolve(key: str) -> TenantContext | None:
        return _CTX if key == _VALID_KEY else None

    app.add_middleware(TenantMiddleware, key_resolver=_resolve)
    app.add_middleware(SecurityHeadersMiddleware)
    app.include_router(tenants_router)
    client = TestClient(app, raise_server_exceptions=False)
    resp = client.put("/tenants/me/llm-config", json={"provider": "gemini"}, headers=H)
    assert resp.status_code == 200
    assert resp.json()["status"] == "saved_in_memory"


def test_save_llm_config_lightweight_swallows_service_exception() -> None:
    svc = AsyncMock()
    svc.save_llm_config.side_effect = RuntimeError("boom")
    client = TestClient(_make_app(svc), raise_server_exceptions=False)
    resp = client.put("/tenants/me/llm-config", json={"provider": "groq"}, headers=H)
    assert resp.status_code == 200
    assert resp.json()["status"] == "saved_in_memory"


def test_save_llm_config_lightweight_malformed_body_defaults_to_empty() -> None:
    """Lines 336-339: an unparsable JSON body degrades to an empty dict, not a 500."""
    client = TestClient(_make_app(), raise_server_exceptions=False)
    resp = client.put(
        "/tenants/me/llm-config",
        content=b"not json",
        headers={**H, "Content-Type": "application/json"},
    )
    assert resp.status_code == 200


# ---------------------------------------------------------------------------
# GET /tenants/me/providers — provider catalog
# ---------------------------------------------------------------------------


def test_get_provider_catalog_lists_known_providers() -> None:
    client = TestClient(_make_app(), raise_server_exceptions=False)
    resp = client.get("/tenants/me/providers", headers=H)
    assert resp.status_code == 200
    providers = resp.json()["providers"]
    names = {p["name"] for p in providers}
    assert {"anthropic", "openai", "ollama"} <= names
    # Never leaks secret values, only the env var name.
    for p in providers:
        assert "api_key" not in p
        assert "value" not in (p.get("env_var") or "")


def test_get_provider_catalog_requires_auth() -> None:
    client = TestClient(_make_app(), raise_server_exceptions=False)
    resp = client.get("/tenants/me/providers")
    assert resp.status_code == 401


def test_get_provider_catalog_reflects_configured_env_key(monkeypatch: Any) -> None:
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-real-key")
    client = TestClient(_make_app(), raise_server_exceptions=False)
    resp = client.get("/tenants/me/providers", headers=H)
    assert resp.status_code == 200
    providers = {p["name"]: p for p in resp.json()["providers"]}
    assert providers["anthropic"]["configured"] is True
    # Ollama needs no API key at all.
    assert providers["ollama"]["configured"] is True


# ---------------------------------------------------------------------------
# GET /tenants/me/members
# ---------------------------------------------------------------------------


def test_list_members_no_db_returns_empty() -> None:
    client = TestClient(_make_app(), raise_server_exceptions=False)
    resp = client.get("/tenants/me/members", headers=H)
    assert resp.status_code == 200
    body = resp.json()
    assert body["members"] == []
    assert body["tenant_id"] == _CTX.tenant_id


def test_list_members_requires_auth() -> None:
    client = TestClient(_make_app(), raise_server_exceptions=False)
    resp = client.get("/tenants/me/members")
    assert resp.status_code == 401


def test_list_members_with_db_returns_joined_rows() -> None:
    membership = MagicMock(
        id="m1", role="admin", status="active", invited_by=None, created_at=None
    )
    user = MagicMock(id="u1", email="member@example.com", name="Member One")
    app = _make_app()
    app.state.db_session_factory = _make_db_mock(rows=[(membership, user)])
    client = TestClient(app, raise_server_exceptions=False)
    resp = client.get("/tenants/me/members", headers=H)
    assert resp.status_code == 200
    body = resp.json()
    assert body["tenant_id"] == _CTX.tenant_id
    assert len(body["members"]) == 1
    assert body["members"][0]["email"] == "member@example.com"
    assert body["members"][0]["role"] == "admin"


# ---------------------------------------------------------------------------
# POST /tenants/me/members/invite — DB-backed paths
# ---------------------------------------------------------------------------


def test_invite_member_new_user_persists_pending_membership() -> None:
    """Lines 701-756: brand-new email creates a User + pending TenantMembership."""
    app = _make_app()
    # scalar_results order: [RLS SET_CONFIG call (unused), User lookup, Membership lookup]
    # both lookups miss -> a new User and a new pending TenantMembership are created.
    app.state.db_session_factory = _make_db_mock(scalar_results=[None, None, None])
    client = TestClient(app, raise_server_exceptions=False)
    resp = client.post(
        "/tenants/me/members/invite",
        json={"email": "brandnew@example.com", "role": "viewer"},
        headers=H,
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "invited"
    assert body["email"] == "brandnew@example.com"
    assert body["role"] == "viewer"
    assert body["tenant_id"] == _CTX.tenant_id


def test_invite_member_existing_membership_reopens_invite() -> None:
    """Re-inviting an existing member updates role/status instead of duplicating."""
    existing_user = MagicMock(id="u-existing")
    existing_membership = MagicMock(id="m-existing", role="viewer", status="active")
    app = _make_app()
    # scalar_results order: [RLS SET_CONFIG call (unused), User lookup, Membership lookup]
    app.state.db_session_factory = _make_db_mock(
        scalar_results=[None, existing_user, existing_membership]
    )
    client = TestClient(app, raise_server_exceptions=False)
    resp = client.post(
        "/tenants/me/members/invite",
        json={"email": "existing@example.com", "role": "operator"},
        headers=H,
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "invited"
    assert existing_membership.role == "operator"
    assert existing_membership.status == "pending"


def test_invite_member_db_exception_returns_500() -> None:
    app = _make_app()
    app.state.db_session_factory = _make_db_mock(raise_on_execute=RuntimeError("db exploded"))
    client = TestClient(app, raise_server_exceptions=False)
    resp = client.post(
        "/tenants/me/members/invite",
        json={"email": "err@example.com", "role": "viewer"},
        headers=H,
    )
    assert resp.status_code == 500


def test_invite_member_notifies_notification_service_best_effort() -> None:
    """The best-effort notification call must not affect the response even if it raises."""
    app = _make_app()
    app.state.db_session_factory = _make_db_mock(scalar_results=[None, None, None])
    notif = AsyncMock()
    notif.send_invite.side_effect = RuntimeError("smtp down")
    app.state.notification_service = notif
    client = TestClient(app, raise_server_exceptions=False)
    resp = client.post(
        "/tenants/me/members/invite",
        json={"email": "notify@example.com", "role": "viewer"},
        headers=H,
    )
    assert resp.status_code == 200
    notif.send_invite.assert_called_once()


# ---------------------------------------------------------------------------
# Notification preferences
# ---------------------------------------------------------------------------


def test_get_notifications_defaults_without_redis() -> None:
    client = TestClient(_make_app(), raise_server_exceptions=False)
    resp = client.get("/tenants/me/notifications", headers=H)
    assert resp.status_code == 200
    body = resp.json()
    assert body["goalComplete"] is True
    assert body["weeklyReport"] is False


def test_get_notifications_requires_auth() -> None:
    client = TestClient(_make_app(), raise_server_exceptions=False)
    resp = client.get("/tenants/me/notifications")
    assert resp.status_code == 401


def test_update_then_get_notifications_round_trips_via_redis() -> None:
    app = _make_app()
    app.state._redis = _FakeRedis()
    client = TestClient(app, raise_server_exceptions=False)
    put_resp = client.put(
        "/tenants/me/notifications",
        json={"goalComplete": False, "weeklyReport": True},
        headers=H,
    )
    assert put_resp.status_code == 200
    assert put_resp.json()["status"] == "updated"

    get_resp = client.get("/tenants/me/notifications", headers=H)
    assert get_resp.status_code == 200
    body = get_resp.json()
    assert body["goalComplete"] is False
    assert body["weeklyReport"] is True


def test_get_notifications_falls_back_to_defaults_on_redis_error() -> None:
    app = _make_app()
    app.state._redis = _RaisingRedis()
    client = TestClient(app, raise_server_exceptions=False)
    resp = client.get("/tenants/me/notifications", headers=H)
    assert resp.status_code == 200
    assert resp.json()["goalComplete"] is True


def test_update_notifications_swallows_redis_error() -> None:
    app = _make_app()
    app.state._redis = _RaisingRedis()
    client = TestClient(app, raise_server_exceptions=False)
    resp = client.put("/tenants/me/notifications", json={"goalComplete": False}, headers=H)
    assert resp.status_code == 200
    assert resp.json()["status"] == "updated"


def test_update_notifications_malformed_body_defaults_to_empty() -> None:
    client = TestClient(_make_app(), raise_server_exceptions=False)
    resp = client.put(
        "/tenants/me/notifications",
        content=b"not json",
        headers={**H, "Content-Type": "application/json"},
    )
    assert resp.status_code == 200
    assert resp.json()["preferences"] == {}


# ---------------------------------------------------------------------------
# Sessions
# ---------------------------------------------------------------------------


def test_list_sessions_returns_empty_list() -> None:
    client = TestClient(_make_app(), raise_server_exceptions=False)
    resp = client.get("/tenants/me/sessions", headers=H)
    assert resp.status_code == 200
    assert resp.json() == []


def test_list_sessions_requires_auth() -> None:
    client = TestClient(_make_app(), raise_server_exceptions=False)
    resp = client.get("/tenants/me/sessions")
    assert resp.status_code == 401


# ---------------------------------------------------------------------------
# Data export
# ---------------------------------------------------------------------------


def test_export_tenant_data_bare_minimum() -> None:
    """No goal_service/agent_store wired → export still succeeds with empty lists."""
    client = TestClient(_make_app(), raise_server_exceptions=False)
    resp = client.post("/tenants/me/export", headers=H)
    assert resp.status_code == 200
    body = resp.json()
    assert body["tenant_id"] == _CTX.tenant_id
    assert body["goals"] == []
    assert body["agents"] == []
    assert "exported_at" in body


def test_export_tenant_data_requires_auth() -> None:
    client = TestClient(_make_app(), raise_server_exceptions=False)
    resp = client.post("/tenants/me/export")
    assert resp.status_code == 401


def test_export_tenant_data_includes_goals_and_agents() -> None:
    app = _make_app()
    goal_svc = AsyncMock()
    goal_svc.list_goals.return_value = {"goals": [{"id": "g1", "status": "completed"}]}
    app.state.goal_service = goal_svc

    agent_store = MagicMock()
    agent_store.list.return_value = [{"id": "a1", "name": "Agent One"}]
    app.state.agent_store = agent_store

    client = TestClient(app, raise_server_exceptions=False)
    resp = client.post("/tenants/me/export", headers=H)
    assert resp.status_code == 200
    body = resp.json()
    assert body["goals"] == [{"id": "g1", "status": "completed"}]
    assert body["agents"] == [{"id": "a1", "name": "Agent One"}]


def test_export_tenant_data_swallows_goal_service_exception() -> None:
    app = _make_app()
    goal_svc = AsyncMock()
    goal_svc.list_goals.side_effect = RuntimeError("goal service down")
    app.state.goal_service = goal_svc
    client = TestClient(app, raise_server_exceptions=False)
    resp = client.post("/tenants/me/export", headers=H)
    assert resp.status_code == 200
    assert resp.json()["goals"] == []


def test_export_tenant_data_swallows_agent_store_exception() -> None:
    app = _make_app()
    agent_store = MagicMock()
    agent_store.list.side_effect = RuntimeError("agent store down")
    app.state.agent_store = agent_store
    client = TestClient(app, raise_server_exceptions=False)
    resp = client.post("/tenants/me/export", headers=H)
    assert resp.status_code == 200
    assert resp.json()["agents"] == []


def test_export_tenant_data_awaits_async_agent_list() -> None:
    """agent_store.list may be async — the endpoint must await it when awaitable."""
    app = _make_app()
    agent_store = AsyncMock()
    agent_store.list.return_value = [{"id": "a-async"}]
    app.state.agent_store = agent_store
    client = TestClient(app, raise_server_exceptions=False)
    resp = client.post("/tenants/me/export", headers=H)
    assert resp.status_code == 200
    assert resp.json()["agents"] == [{"id": "a-async"}]


# ---------------------------------------------------------------------------
# Account deletion
# ---------------------------------------------------------------------------


def test_delete_tenant_schedules_deletion() -> None:
    client = TestClient(_make_app(), raise_server_exceptions=False)
    resp = client.delete("/tenants/me", headers=H)
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "scheduled_for_deletion"
    assert body["tenant_id"] == _CTX.tenant_id


def test_delete_tenant_requires_auth() -> None:
    client = TestClient(_make_app(), raise_server_exceptions=False)
    resp = client.delete("/tenants/me")
    assert resp.status_code == 401
