"""Remote A2A agent registry: tenant-scoped, server-side (FE-09).

Regression: remote agents registered on the A2A page lived only in the browser's
localStorage, so other operators (and the backend) never saw them, and the browser
fetched arbitrary agent-card URLs itself. The registry is now
``/a2a/remote-agents`` backed by the RLS-protected ``a2a_remote_agents`` table;
the server fetches and validates the agent card (SSRF-guarded) on register/ping.
"""

from __future__ import annotations

import json
import uuid
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

import app.api.a2a_remote_agents as mod
from app.tenancy.context import PlanTier, TenantContext
from tests._rls_recorder import RlsRecordingDb, assert_tenant_scoped

_TID_A = uuid.uuid4().hex
_TID_B = uuid.uuid4().hex
_CARD = {"name": "Research Bot", "version": "1.2.0", "supported_task_types": ["research"]}


class _FakeResponse:
    def __init__(self, status_code: int, body: Any) -> None:
        self.status_code = status_code
        self.content = body if isinstance(body, bytes) else json.dumps(body).encode()

    def json(self) -> Any:
        return json.loads(self.content)


class _FakeClient:
    """Stands in for public_async_client(); maps URL -> response (or exception)."""

    responses: dict[str, Any] = {}
    calls: list[str] = []

    def __init__(self, **_kw: Any) -> None:
        pass

    async def __aenter__(self) -> _FakeClient:
        return self

    async def __aexit__(self, *_exc: object) -> None:
        return None

    async def get(self, url: str, **_kw: Any) -> _FakeResponse:
        _FakeClient.calls.append(url)
        r = _FakeClient.responses.get(url)
        if isinstance(r, Exception):
            raise r
        if r is None:
            raise ConnectionError("unreachable")
        return r


@pytest.fixture(autouse=True)
def _fake_network(monkeypatch: pytest.MonkeyPatch) -> None:
    async def _public(_url: str, **_kw: Any) -> list[str]:
        return ["93.184.216.34"]

    _FakeClient.responses = {}
    _FakeClient.calls = []
    monkeypatch.setattr(mod, "public_async_client", _FakeClient)
    monkeypatch.setattr(mod, "assert_public_url_async", _public)
    mod._memory.clear()


def _app(tenant_id: str, db: Any = None) -> FastAPI:
    app = FastAPI()

    @app.middleware("http")
    async def _tenant(request: Any, call_next: Any) -> Any:
        request.state.tenant = TenantContext(
            tenant_id=tenant_id, plan=PlanTier.ENTERPRISE, api_key_id="k"
        )
        return await call_next(request)

    app.include_router(mod.router)
    app.state.db_session_factory = db
    return app


def _client(tenant_id: str, db: Any = None) -> TestClient:
    return TestClient(_app(tenant_id, db))


URL = "https://agents.example.com/.well-known/agent.json"


def test_register_validates_the_card_server_side_and_lists_it() -> None:
    _FakeClient.responses[URL] = _FakeResponse(200, _CARD)
    c = _client(_TID_A)
    r = c.post("/a2a/remote-agents", json={"url": URL})
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["name"] == "Research Bot"  # defaulted from the card
    assert body["card"]["version"] == "1.2.0"
    assert body["last_error"] is None
    assert _FakeClient.calls == [URL]

    listed = c.get("/a2a/remote-agents").json()
    assert [a["id"] for a in listed["agents"]] == [body["id"]]


def test_registry_is_tenant_scoped() -> None:
    _FakeClient.responses[URL] = _FakeResponse(200, _CARD)
    a = _client(_TID_A)
    agent_id = a.post("/a2a/remote-agents", json={"url": URL, "name": "Mine"}).json()["id"]

    b = _client(_TID_B)
    assert b.get("/a2a/remote-agents").json()["agents"] == []
    assert b.delete(f"/a2a/remote-agents/{agent_id}").status_code == 404
    assert b.post(f"/a2a/remote-agents/{agent_id}/ping").status_code == 404
    # Still there for its owner.
    assert len(a.get("/a2a/remote-agents").json()["agents"]) == 1


@pytest.mark.parametrize(
    ("response", "needle"),
    [
        (_FakeResponse(200, {"version": "1"}), "name"),
        (_FakeResponse(200, ["not", "an", "object"]), "name"),
        (_FakeResponse(200, b"<html>nope</html>"), "JSON"),
        (_FakeResponse(404, {"detail": "nope"}), "404"),
        (ConnectionError("down"), "could not be fetched"),
    ],
)
def test_an_invalid_or_unreachable_card_is_refused_and_not_stored(
    response: Any, needle: str
) -> None:
    _FakeClient.responses[URL] = response
    c = _client(_TID_A)
    r = c.post("/a2a/remote-agents", json={"url": URL})
    assert r.status_code == 422
    assert needle in r.json()["detail"]
    assert c.get("/a2a/remote-agents").json()["agents"] == []


def test_a_private_url_is_refused_by_the_ssrf_guard(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.net.ssrf_guard import assert_public_url_async

    monkeypatch.setattr(mod, "assert_public_url_async", assert_public_url_async)
    c = _client(_TID_A)
    r = c.post("/a2a/remote-agents", json={"url": "http://169.254.169.254/latest/meta-data"})
    assert r.status_code == 422
    assert _FakeClient.calls == []


def test_registering_the_same_url_twice_is_a_conflict() -> None:
    _FakeClient.responses[URL] = _FakeResponse(200, _CARD)
    c = _client(_TID_A)
    assert c.post("/a2a/remote-agents", json={"url": URL}).status_code == 201
    assert c.post("/a2a/remote-agents", json={"url": URL}).status_code == 409


def test_ping_refreshes_the_card_and_records_a_failure() -> None:
    _FakeClient.responses[URL] = _FakeResponse(200, _CARD)
    c = _client(_TID_A)
    agent_id = c.post("/a2a/remote-agents", json={"url": URL}).json()["id"]

    _FakeClient.responses[URL] = _FakeResponse(200, {**_CARD, "version": "2.0.0"})
    r = c.post(f"/a2a/remote-agents/{agent_id}/ping")
    assert r.status_code == 200
    assert r.json()["card"]["version"] == "2.0.0"
    assert r.json()["last_error"] is None

    _FakeClient.responses[URL] = ConnectionError("down")
    r = c.post(f"/a2a/remote-agents/{agent_id}/ping")
    assert r.status_code == 200
    assert r.json()["last_error"]
    # The last good card is kept; the failure is recorded, not hidden.
    assert r.json()["card"]["version"] == "2.0.0"
    assert c.get("/a2a/remote-agents").json()["agents"][0]["last_error"]


def test_delete_removes_the_agent() -> None:
    _FakeClient.responses[URL] = _FakeResponse(200, _CARD)
    c = _client(_TID_A)
    agent_id = c.post("/a2a/remote-agents", json={"url": URL}).json()["id"]
    assert c.delete(f"/a2a/remote-agents/{agent_id}").status_code == 204
    assert c.get("/a2a/remote-agents").json()["agents"] == []
    assert c.delete(f"/a2a/remote-agents/{agent_id}").status_code == 404


# ── DB mode: every statement runs under the tenant GUC with a tenant predicate ──


class _Table:
    def __init__(self) -> None:
        self.rows: dict[str, dict[str, Any]] = {}

    def rows_for(self, sql: str, p: dict[str, Any]) -> list[Any]:
        if sql.startswith("INSERT INTO a2a_remote_agents"):
            if any(r["tid"] == p["tid"] and r["url"] == p["url"] for r in self.rows.values()):
                return []
            self.rows[p["id"]] = dict(p)
            return [self._tuple(self.rows[p["id"]])]
        if sql.startswith("SELECT") and "a2a_remote_agents" in sql:
            return [self._tuple(r) for r in self.rows.values() if r["tid"] == p["tid"]]
        if sql.startswith("DELETE FROM a2a_remote_agents"):
            r = self.rows.get(p["id"])
            if r and r["tid"] == p["tid"]:
                del self.rows[p["id"]]
                return [(p["id"],)]
            return []
        return []

    @staticmethod
    def _tuple(r: dict[str, Any]) -> tuple[Any, ...]:
        return (r["id"], r["name"], r["url"], json.loads(r["card"]), None, None, None)


def test_db_mode_runs_every_statement_tenant_scoped() -> None:
    table = _Table()
    db = RlsRecordingDb(rows_for=table.rows_for)
    _FakeClient.responses[URL] = _FakeResponse(200, _CARD)
    c = _client(_TID_A, db)
    r = c.post("/a2a/remote-agents", json={"url": URL})
    assert r.status_code == 201, r.text
    assert len(c.get("/a2a/remote-agents").json()["agents"]) == 1
    assert c.delete(f"/a2a/remote-agents/{r.json()['id']}").status_code == 204
    # Nothing lived in process memory.
    assert mod._memory == {}
    assert_tenant_scoped(db, "a2a_remote_agents", _TID_A)
