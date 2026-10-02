"""NATIVE-02: the platform email relay is bounded.

Any tools:write key could send unlimited mail to unlimited recipients from the
platform sender. Now: operator/admin only, at most email_max_recipients per
message, and a per-tenant daily recipient quota shared through Redis.
"""

from __future__ import annotations

import uuid
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.core.config import get_settings
from app.governance.audit import AuditLog
from app.tenancy.context import PlanTier, TenantContext


class _QuotaRedis:
    """Implements the quota Lua script's semantics (fakeredis has no EVAL)."""

    def __init__(self) -> None:
        self.counts: dict[str, int] = {}

    # Redis EVAL (server-side Lua), not Python eval.
    async def eval(self, _script: str, _n: int, key: str, n: str, limit: str, _ttl: str) -> Any:
        used = self.counts.get(key, 0)
        if used + int(n) > int(limit):
            return [0, used]
        self.counts[key] = used + int(n)
        return [1, used + int(n)]


class _Down:
    async def eval(self, *_a: Any) -> Any:
        raise ConnectionError("redis down")


def _client(redis: Any, roles: tuple[str, ...] = ("operator",)) -> tuple[TestClient, list[Any]]:
    from app.api.tools import router

    ctx = TenantContext(
        tenant_id=f"t-{uuid.uuid4().hex[:8]}", plan=PlanTier.FREE, api_key_id="k", roles=roles
    )
    app = FastAPI()

    @app.middleware("http")
    async def _inject(request: Any, call_next: Any) -> Any:
        request.state.tenant = ctx
        return await call_next(request)

    app.include_router(router)
    app.state.audit_log = AuditLog()
    app.state._redis = redis
    return TestClient(app), []


@pytest.fixture(autouse=True)
def _fake_smtp(monkeypatch: pytest.MonkeyPatch) -> list[Any]:
    sent: list[Any] = []

    async def _send(to: Any, subject: str, body: str, **_kw: Any) -> dict[str, Any]:
        sent.append(to)
        return {"success": True, "to": to, "subject": subject}

    monkeypatch.setattr("app.tools.email_tool.email_send", _send)
    return sent


@pytest.fixture
def quota3(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("EMAIL_DAILY_RECIPIENT_QUOTA", "3")
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


def _send(client: TestClient, to: Any) -> Any:
    return client.post("/tools/email/send", json={"to": to, "subject": "s", "body": "b"})


def test_more_than_max_recipients_is_422(_fake_smtp: list[Any]) -> None:
    client, _ = _client(_QuotaRedis())
    resp = _send(client, [f"u{i}@example.com" for i in range(51)])
    assert resp.status_code == 422
    assert _fake_smtp == []


def test_viewer_keys_cannot_relay_mail(_fake_smtp: list[Any]) -> None:
    client, _ = _client(_QuotaRedis(), roles=("viewer",))
    assert _send(client, "a@example.com").status_code == 403
    assert _fake_smtp == []


def test_daily_quota_is_enforced_and_reported(quota3: None, _fake_smtp: list[Any]) -> None:
    client, _ = _client(_QuotaRedis())
    first = _send(client, ["a@example.com", "b@example.com"])
    assert first.status_code == 200, first.text
    assert first.json()["quota_remaining"] == 1
    over = _send(client, ["c@example.com", "d@example.com"])
    assert over.status_code == 429
    assert "quota" in over.json()["detail"].lower()
    assert len(_fake_smtp) == 1
    assert _send(client, "c@example.com").json()["quota_remaining"] == 0


def test_quota_store_outage_refuses_outside_development(
    monkeypatch: pytest.MonkeyPatch, _fake_smtp: list[Any]
) -> None:
    monkeypatch.setenv("ENVIRONMENT", "staging")
    get_settings.cache_clear()
    try:
        client, _ = _client(_Down())
        assert _send(client, "a@example.com").status_code == 503
        client2, _ = _client(None)
        assert _send(client2, "a@example.com").status_code == 503
    finally:
        monkeypatch.setenv("ENVIRONMENT", "development")
        get_settings.cache_clear()
    assert _fake_smtp == []
