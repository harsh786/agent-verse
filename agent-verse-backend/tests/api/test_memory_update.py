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


# ── Regression: PATCH / POST skipped the MEMORY_WRITE guardrail + re-embedding ──
#
# PATCH rewrote ``content`` straight into long_term_memory: no MEMORY_WRITE
# guardrail (every other write path — LongTermMemoryStore.store_async, chat,
# goal learning — screens it), and the row kept the OLD content's embedding, so
# semantic recall matched text that no longer exists. POST /memory inserted
# with neither screening nor an embedding at all.


class _Embedder:
    def __init__(self) -> None:
        self.texts: list[str] = []

    async def embed(self, request: Any) -> Any:
        from app.providers.base import EmbedResponse

        self.texts.extend(request.texts)
        return EmbedResponse(embeddings=[[0.25] * 8 for _ in request.texts], model="emb-1")


def _guardrail(monkeypatch: Any, result: Any) -> None:
    from app.guardrails_v2.engine import guardrails_engine

    async def _evaluate(*_a: Any, **_k: Any) -> Any:
        if isinstance(result, Exception):
            raise result
        return result

    monkeypatch.setattr(guardrails_engine, "evaluate", _evaluate)


def _updates(db: _RecordingDB) -> list[tuple[str, dict[str, Any]]]:
    return [(s, p) for s, p, _ in db.statements if "UPDATE long_term_memory" in s]


def test_patch_content_blocked_by_memory_write_guardrail_is_422(monkeypatch: Any) -> None:
    _guardrail(monkeypatch, {"blocked": True, "redacted_content": None})
    db = _RecordingDB(("m1", "x", "fact", 0.5, [], _CREATED))
    r = TestClient(_app(db)).patch("/memory/m1", json={"content": "secret"}, headers=_H)
    assert r.status_code == 422, r.text
    assert "guardrail" in r.json()["detail"].lower()
    assert _updates(db) == []


def test_patch_content_guardrail_unavailable_is_503(monkeypatch: Any) -> None:
    _guardrail(monkeypatch, RuntimeError("rules could not be loaded"))
    db = _RecordingDB(("m1", "x", "fact", 0.5, [], _CREATED))
    r = TestClient(_app(db)).patch("/memory/m1", json={"content": "text"}, headers=_H)
    assert r.status_code == 503, r.text
    assert _updates(db) == []


def test_patch_content_stores_the_redacted_text_and_re_embeds_it(monkeypatch: Any) -> None:
    _guardrail(monkeypatch, {"blocked": False, "redacted_content": "call [REDACTED]"})
    db = _RecordingDB(("m1", "call [REDACTED]", "fact", 0.5, [], _CREATED))
    app = _app(db)
    embedder = _Embedder()
    app.state.embedder = embedder
    r = TestClient(app).patch("/memory/m1", json={"content": "call 555-0100"}, headers=_H)
    assert r.status_code == 200, r.text
    [(sql, params)] = _updates(db)
    assert params["content"] == "call [REDACTED]"
    assert embedder.texts == ["call [REDACTED]"]  # the stored text, not the raw one
    assert "embedding = CAST(:emb AS vector)" in sql
    assert params["emodel"] == "emb-1" and params["edim"] == 8


def test_patch_content_without_embedder_clears_the_stale_vector(monkeypatch: Any) -> None:
    _guardrail(monkeypatch, {"blocked": False, "redacted_content": "new text"})
    db = _RecordingDB(("m1", "new text", "fact", 0.5, [], _CREATED))
    r = TestClient(_app(db)).patch("/memory/m1", json={"content": "new text"}, headers=_H)
    assert r.status_code == 200, r.text
    [(sql, _params)] = _updates(db)
    assert "embedding = NULL" in sql


def test_patch_without_content_does_not_touch_the_embedding(monkeypatch: Any) -> None:
    _guardrail(monkeypatch, RuntimeError("must not be consulted"))
    db = _RecordingDB(("m1", "x", "fact", 0.9, [], _CREATED))
    r = TestClient(_app(db)).patch("/memory/m1", json={"confidence": 0.9}, headers=_H)
    assert r.status_code == 200, r.text
    [(sql, _params)] = _updates(db)
    assert "embedding" not in sql


def test_patch_in_memory_store_is_screened_too(monkeypatch: Any) -> None:
    _guardrail(monkeypatch, {"blocked": True, "redacted_content": None})
    mem = SimpleNamespace(
        id="m1", memory_id="m1", content="old", memory_type="fact", confidence=0.8, tags=[]
    )
    ltm = SimpleNamespace(_memories={_CTX.tenant_id: [mem]})
    r = TestClient(_app(None, ltm)).patch("/memory/m1", json={"content": "bad"}, headers=_H)
    assert r.status_code == 422
    assert mem.content == "old"


def test_create_memory_is_screened_and_embedded(monkeypatch: Any) -> None:
    _guardrail(monkeypatch, {"blocked": False, "redacted_content": "my [REDACTED]"})
    db = _RecordingDB(None)
    app = _app(db)
    embedder = _Embedder()
    app.state.embedder = embedder
    r = TestClient(app).post("/memory", json={"content": "my ssn 123-45-6789"}, headers=_H)
    assert r.status_code == 201, r.text
    assert r.json()["content"] == "my [REDACTED]"
    inserts = [(s, p) for s, p, _ in db.statements if "INSERT INTO long_term_memory" in s]
    [(sql, params)] = inserts
    assert params["content"] == "my [REDACTED]"
    assert "CAST(:emb AS vector)" in sql and embedder.texts == ["my [REDACTED]"]


def test_create_memory_blocked_by_guardrail_is_422(monkeypatch: Any) -> None:
    _guardrail(monkeypatch, {"blocked": True, "redacted_content": None})
    db = _RecordingDB(None)
    r = TestClient(_app(db)).post("/memory", json={"content": "secret"}, headers=_H)
    assert r.status_code == 422, r.text
    assert not [s for s, _p, _ in db.statements if "INSERT" in s]
