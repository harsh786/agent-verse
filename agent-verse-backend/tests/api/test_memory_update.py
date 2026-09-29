"""PATCH /memory/{id} — the memory explorer's "Edit memory" called it, but the
route did not exist, so every edit failed (405)."""

from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace
from typing import Any

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.memory import router as memory_router
from app.tenancy.context import PlanTier, TenantContext
from app.tenancy.middleware import TenantMiddleware
from tests.api.test_memory_db_authoritative import _BrokenDB

_CTX = TenantContext(tenant_id="tid-mem-patch", plan=PlanTier.PROFESSIONAL, api_key_id="k")
_H = {"X-API-Key": "ak_mem_patch"}
_CREATED = datetime(2026, 1, 1, tzinfo=UTC)


class _Result:
    def __init__(self, row: Any) -> None:
        self._row = row

    def fetchone(self) -> Any:
        return self._row


class _Session:
    def __init__(self, rec: _RecordingDB) -> None:
        self._rec = rec

    async def __aenter__(self) -> _Session:
        return self

    async def __aexit__(self, *a: Any) -> None:
        return None

    def begin(self) -> _Session:
        return self

    async def execute(self, stmt: Any, params: dict[str, Any] | None = None) -> _Result:
        sql = str(stmt)
        params = params or {}
        if "set_config" in sql:
            self._rec.guc = params.get("tid")
            return _Result(None)
        self._rec.statements.append((sql, params, self._rec.guc))
        return _Result(self._rec.row)


class _RecordingDB:
    def __init__(self, row: Any) -> None:
        self.row = row
        self.guc: str | None = None
        self.statements: list[tuple[str, dict[str, Any], str | None]] = []

    def __call__(self) -> _Session:
        return _Session(self)


def _app(db: Any = None, ltm: Any = None) -> FastAPI:
    app = FastAPI()

    async def _resolve(key: str) -> TenantContext | None:
        return _CTX if key == "ak_mem_patch" else None

    app.add_middleware(TenantMiddleware, key_resolver=_resolve)
    app.include_router(memory_router)
    app.state.db_session_factory = db
    app.state.long_term_memory = ltm
    return app


def test_patch_updates_the_row_under_tenant_rls() -> None:
    db = _RecordingDB(("m1", "new text", "fact", 0.5, ["a"], _CREATED))
    r = TestClient(_app(db)).patch(
        "/memory/m1", json={"content": "new text", "confidence": 0.5, "tags": ["a"]}, headers=_H
    )
    assert r.status_code == 200, r.text
    assert r.json() == {
        "id": "m1",
        "content": "new text",
        "memory_type": "fact",
        "confidence": 0.5,
        "tags": ["a"],
        "created_at": _CREATED.isoformat(),
    }
    (sql, params, guc) = db.statements[-1]
    assert sql.lstrip().startswith("UPDATE long_term_memory")
    assert "tenant_id = :tid" in sql
    assert "memory_type" not in sql.split("RETURNING")[0]  # only sent fields are updated
    assert params["tid"] == _CTX.tenant_id
    assert guc == _CTX.tenant_id


def test_patch_unknown_memory_is_404() -> None:
    r = TestClient(_app(_RecordingDB(None))).patch(
        "/memory/nope", json={"content": "x"}, headers=_H
    )
    assert r.status_code == 404


def test_patch_db_failure_is_503() -> None:
    r = TestClient(_app(_BrokenDB())).patch("/memory/m1", json={"content": "x"}, headers=_H)
    assert r.status_code == 503


def test_patch_rejects_an_empty_body_and_bad_confidence() -> None:
    client = TestClient(_app(_RecordingDB(None)))
    assert client.patch("/memory/m1", json={}, headers=_H).status_code == 422
    assert client.patch("/memory/m1", json={"confidence": 2}, headers=_H).status_code == 422


def test_patch_without_db_edits_the_in_memory_store() -> None:
    mem = SimpleNamespace(
        id="m1", memory_id="m1", content="old", memory_type="fact", confidence=0.8, tags=[]
    )
    ltm = SimpleNamespace(_memories={_CTX.tenant_id: [mem]})
    client = TestClient(_app(None, ltm))
    r = client.patch("/memory/m1", json={"content": "edited"}, headers=_H)
    assert r.status_code == 200
    assert mem.content == "edited"
    assert client.patch("/memory/other", json={"content": "x"}, headers=_H).status_code == 404
