"""a02-F040-11: a signature is single-use across replicas even without Redis.

Without ``app.state._redis`` the replay cache was the per-process
``_seen_signatures`` dict, so on a multi-replica deployment each replica
accepted a captured signature once. With a database the accepted task row now
carries the signature under a unique index (any replica, any tenant); with
neither Redis nor a database, production refuses signed tasks (503).
"""

from __future__ import annotations

import json
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy.exc import IntegrityError

from app.api import a2a as a2a_mod
from app.api.a2a import _seen_signatures, _tasks, sign_a2a_payload
from app.api.a2a import router as a2a_router
from app.tenancy.context import PlanTier, TenantContext
from tests.api._a2a_fakes import FakeGoalService

CALLER = TenantContext(tenant_id="a2a-replay", plan=PlanTier.FREE, api_key_id="k")
SECRET = "test-" "a2a-secret"


class _UniqueSignatureDb:
    """Session factory whose a2a_tasks INSERT enforces a unique hmac_signature."""

    def __init__(self) -> None:
        self.signatures: set[str] = set()
        self.inserts = 0

    def __call__(self) -> _UniqueSignatureDb:
        return self

    async def __aenter__(self) -> _UniqueSignatureDb:
        return self

    async def __aexit__(self, *exc: object) -> None:
        return None

    def begin(self) -> _UniqueSignatureDb:
        return self

    async def execute(self, stmt: Any, params: dict[str, Any] | None = None) -> Any:
        sql = str(stmt)
        if "INSERT INTO a2a_tasks" in sql:
            sig = (params or {}).get("sig")
            if sig is not None and sig in self.signatures:
                raise IntegrityError(
                    sql, params, Exception('duplicate key violates "uq_a2a_tasks_hmac_signature"')
                )
            if sig is not None:
                self.signatures.add(sig)
            self.inserts += 1

        class _R:
            def fetchone(self) -> None:
                return None

        return _R()


def _app(db: Any) -> FastAPI:
    app = FastAPI()
    app.state.db_session_factory = db
    app.state.goal_service = FakeGoalService()

    @app.middleware("http")
    async def _caller(request: Any, call_next: Any) -> Any:
        request.state.tenant = CALLER
        return await call_next(request)

    app.include_router(a2a_router)
    return app


@pytest.fixture(autouse=True)
def _env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("A2A_SHARED_SECRET", SECRET)
    _tasks.clear()
    _seen_signatures.clear()


def _signed() -> tuple[bytes, dict[str, str]]:
    raw = json.dumps({"goal": "Summarise X"}).encode()
    return raw, {"Content-Type": "application/json", **sign_a2a_payload(raw, SECRET)}


def test_a_replay_on_another_replica_is_refused_by_the_shared_database() -> None:
    db = _UniqueSignatureDb()
    raw, headers = _signed()
    replica_a, replica_b = TestClient(_app(db)), TestClient(_app(db))
    assert replica_a.post("/a2a/tasks", content=raw, headers=headers).status_code == 202
    # Each replica has its own process memory; only the database is shared.
    _seen_signatures.clear()
    resp = replica_b.post("/a2a/tasks", content=raw, headers=headers)
    assert resp.status_code == 401
    assert "replayed" in resp.json()["detail"]
    assert db.inserts == 1


def test_production_without_redis_or_database_refuses_signed_tasks(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(a2a_mod, "_is_production", lambda: True)
    raw, headers = _signed()
    resp = TestClient(_app(None)).post("/a2a/tasks", content=raw, headers=headers)
    assert resp.status_code == 503
    assert _tasks == {}


def test_dev_without_redis_or_database_keeps_the_process_cache() -> None:
    raw, headers = _signed()
    client = TestClient(_app(None))
    assert client.post("/a2a/tasks", content=raw, headers=headers).status_code == 202
    assert client.post("/a2a/tasks", content=raw, headers=headers).status_code == 401
