"""Regression: Memory 2.0 with a DB configured is DB-authoritative.

Bugs fixed:
* ``_db_upsert_memory`` swallowed every DB error, so writes (including GDPR
  deletes) reported success without persisting — and every insert did fail,
  because ``str(uuid4())`` (36 chars) does not fit ``long_term_memory.id``
  VARCHAR(32).
* Reads consulted a per-process dict first, so replica B served a memory that
  replica A had deleted, and a PATCH on B wrote it back as active.

``_FakeLtmDb`` emulates the ``long_term_memory`` table (including the 32-char id
limit) and is shared by two app instances standing in for two replicas.
"""

from __future__ import annotations

import copy
import json
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

import app.api.memory_v2 as memory_v2
from app.tenancy.context import PlanTier, TenantContext


class _Result:
    def __init__(self, rows: list[tuple[Any, ...]]) -> None:
        self._rows = rows

    def fetchone(self) -> Any:
        return self._rows[0] if self._rows else None

    def fetchall(self) -> list[Any]:
        return list(self._rows)


class _FakeLtmDb:
    def __init__(self, *, broken: bool = False) -> None:
        self.rows: dict[str, tuple[str, str]] = {}  # id -> (tenant_id, content)
        self.broken = broken

    def __call__(self) -> _FakeSession:
        return _FakeSession(self)


class _Txn:
    async def __aenter__(self) -> None:
        return None

    async def __aexit__(self, *a: object) -> bool:
        return False


class _FakeSession:
    def __init__(self, db: _FakeLtmDb) -> None:
        self.db = db

    async def __aenter__(self) -> _FakeSession:
        return self

    async def __aexit__(self, *a: object) -> bool:
        return False

    def begin(self) -> _Txn:
        return _Txn()

    async def flush(self) -> None:
        return None

    async def commit(self) -> None:
        return None

    async def execute(self, stmt: Any, params: dict[str, Any] | None = None) -> _Result:
        sql = " ".join(str(stmt).split())
        p = params or {}
        if "set_config" in sql:
            return _Result([])
        if self.db.broken:
            raise RuntimeError("connection refused")
        if sql.startswith("INSERT INTO long_term_memory"):
            if len(p["id"]) > 32:
                raise RuntimeError("value too long for type character varying(32)")
            self.db.rows[p["id"]] = (p["tid"], p["content"])
            return _Result([])
        if sql.startswith("UPDATE long_term_memory"):
            if p["id"] in self.db.rows:
                self.db.rows[p["id"]] = (p["tid"], p["content"])
            return _Result([])
        if sql.startswith("SELECT content FROM long_term_memory"):
            if "id = :mid" in sql:
                hit = self.db.rows.get(p["mid"])
                ok = hit is not None and hit[0] == p["tid"]
                return _Result([(hit[1],)] if ok and hit else [])
            return _Result(
                [
                    (c,)
                    for t, c in self.db.rows.values()
                    if t == p["tid"] and json.loads(c).get("lifecycle_state") != "deleted"
                ]
            )
        return _Result([])


def _replica(db: _FakeLtmDb) -> TestClient:
    app = FastAPI()
    app.include_router(memory_v2.router)
    app.state.db = db
    app.state.db_session_factory = db

    @app.middleware("http")
    async def _inject(request: Any, call_next: Any) -> Any:
        request.state.tenant = TenantContext(
            tenant_id="t-mem", plan=PlanTier.ENTERPRISE, api_key_id="k"
        )
        return await call_next(request)

    return TestClient(app)


@pytest.fixture(autouse=True)
def _clean_module_state() -> Any:
    memory_v2._memories.clear()
    memory_v2._conflicts.clear()
    loaded = getattr(memory_v2, "_db_loaded_tenants", None)
    if loaded is not None:
        loaded.clear()
    yield
    memory_v2._memories.clear()


def test_create_is_durable_and_visible_to_another_replica() -> None:
    db = _FakeLtmDb()
    a, b = _replica(db), _replica(db)
    mid = a.post("/memory-v2", json={"content": "durable"}).json()["memory_id"]
    assert len(db.rows) == 1, "memory never reached the database"
    memory_v2._memories.clear()  # replica B has its own (empty) process memory
    assert b.get(f"/memory-v2/{mid}").status_code == 200


def test_delete_on_one_replica_is_honoured_by_another() -> None:
    db = _FakeLtmDb()
    a, b = _replica(db), _replica(db)
    mid = a.post("/memory-v2", json={"content": "personal data"}).json()["memory_id"]
    assert b.get(f"/memory-v2/{mid}").status_code == 200
    replica_b_process_state = copy.deepcopy(memory_v2._memories)  # B's warm cache

    assert a.delete(f"/memory-v2/{mid}").status_code == 200

    memory_v2._memories.clear()
    memory_v2._memories.update(replica_b_process_state)  # now "running on B"
    assert b.get(f"/memory-v2/{mid}").status_code == 404
    assert b.patch(f"/memory-v2/{mid}", json={"content": "revived"}).status_code == 404
    stored = [json.loads(c) for _, c in db.rows.values()]
    assert stored and all(m["lifecycle_state"] == "deleted" for m in stored)


@pytest.mark.parametrize(
    ("method", "path", "body"),
    [
        ("post", "/memory-v2", {"content": "x"}),
        ("get", "/memory-v2", None),
        ("get", "/memory-v2/export/gdpr", None),
        ("get", "/memory-v2/abc", None),
        ("patch", "/memory-v2/abc", {"content": "y"}),
        ("delete", "/memory-v2/abc", None),
    ],
)
def test_db_failure_is_503_not_fake_success(method: str, path: str, body: Any) -> None:
    client = _replica(_FakeLtmDb(broken=True))
    kwargs = {"json": body} if body is not None else {}
    resp = getattr(client, method)(path, **kwargs)
    assert resp.status_code == 503, (method, path, resp.status_code, resp.text)
