"""MEM-03: editing a long-term memory is screened and re-embedded on every route.

PATCH /chat/memories/{id} used to call ``update_content_async``, which rewrote
the content with no MEMORY_WRITE screening and kept the OLD vector.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import patch

import pytest
from fastapi import FastAPI, Request
from fastapi.testclient import TestClient

from app.memory.long_term import (
    LongTermMemoryBlockedError,
    LongTermMemoryStore,
    LongTermMemoryUnavailableError,
)
from app.providers.base import EmbedResponse
from app.tenancy.context import PlanTier, TenantContext
from tests._rls_recorder import RlsRecordingDb

TENANT = "tenant-ltm-edit"


def _ctx() -> TenantContext:
    return TenantContext(tenant_id=TENANT, plan=PlanTier.PROFESSIONAL, api_key_id="k1")


def _store(db: Any) -> LongTermMemoryStore:
    s = LongTermMemoryStore()
    s._db_factory = db
    return s


async def _verdict(*, content: str, **_kw: Any) -> dict[str, Any]:
    if "SECRET" in content:
        return {"blocked": True}
    if "ssn 123" in content:
        return {"blocked": False, "redacted_content": content.replace("ssn 123", "[REDACTED]")}
    return {"blocked": False}


async def _guardrail_down(**_kw: Any) -> dict[str, Any]:
    raise RuntimeError("guardrail engine down")


class _Embedder:
    async def embed(self, request: Any) -> EmbedResponse:
        return EmbedResponse(embeddings=[[0.5] * 8], model="fake-embed")


def _updates(db: RlsRecordingDb) -> list[Any]:
    return [s for s in db.statements if s.sql.lstrip().upper().startswith("UPDATE")]


async def test_blocked_edit_raises_and_writes_nothing() -> None:
    db = RlsRecordingDb(rows_for=lambda sql, _p: [1] if sql.startswith("UPDATE") else [])
    with (
        patch("app.guardrails_v2.engine.guardrails_engine.evaluate", side_effect=_verdict),
        pytest.raises(LongTermMemoryBlockedError),
    ):
        await _store(db).update_content_async(
            memory_id="m1", content="the SECRET key", tenant_ctx=_ctx()
        )
    assert _updates(db) == []


async def test_guardrail_outage_fails_closed() -> None:
    db = RlsRecordingDb(rows_for=lambda sql, _p: [1] if sql.startswith("UPDATE") else [])
    with (
        patch("app.guardrails_v2.engine.guardrails_engine.evaluate", side_effect=_guardrail_down),
        pytest.raises(LongTermMemoryUnavailableError),
    ):
        await _store(db).update_content_async(memory_id="m1", content="ok", tenant_ctx=_ctx())
    assert _updates(db) == []


async def test_allowed_edit_is_redacted_and_re_embedded() -> None:
    db = RlsRecordingDb(rows_for=lambda sql, _p: [1] if sql.startswith("UPDATE") else [])
    with patch("app.guardrails_v2.engine.guardrails_engine.evaluate", side_effect=_verdict):
        m = await _store(db).update_content_async(
            memory_id="m1", content="my ssn 123 is here", tenant_ctx=_ctx(), embedder=_Embedder()
        )
    assert m is not None and m.content == "my [REDACTED] is here"
    (upd,) = _updates(db)
    assert upd.params["c"] == "my [REDACTED] is here"
    assert "embedding = CAST(:emb AS vector)" in upd.sql
    assert upd.params["emodel"] == "fake-embed"


async def test_edit_without_embedder_clears_the_stale_vector() -> None:
    db = RlsRecordingDb(rows_for=lambda sql, _p: [1] if sql.startswith("UPDATE") else [])
    with patch("app.guardrails_v2.engine.guardrails_engine.evaluate", side_effect=_verdict):
        await _store(db).update_content_async(memory_id="m1", content="new", tenant_ctx=_ctx())
    (upd,) = _updates(db)
    assert "embedding = NULL" in upd.sql


def _chat_client(ltm: LongTermMemoryStore) -> TestClient:
    from app.chat.router import router

    app = FastAPI()

    @app.middleware("http")
    async def inject(request: Request, call_next: Any) -> Any:
        request.state.tenant = _ctx()
        request.app.state.long_term_memory = ltm
        return await call_next(request)

    app.include_router(router, prefix="/api/v1")
    return TestClient(app, raise_server_exceptions=False)


def test_chat_patch_maps_block_to_422_and_outage_to_503() -> None:
    db = RlsRecordingDb(rows_for=lambda sql, _p: [1] if sql.startswith("UPDATE") else [])
    client = _chat_client(_store(db))
    with patch("app.guardrails_v2.engine.guardrails_engine.evaluate", side_effect=_verdict):
        blocked = client.patch("/api/v1/chat/memories/m1", json={"content": "SECRET stuff"})
        ok = client.patch("/api/v1/chat/memories/m1", json={"content": "fine"})
    assert blocked.status_code == 422, blocked.text
    assert ok.status_code == 200, ok.text
    with patch("app.guardrails_v2.engine.guardrails_engine.evaluate", side_effect=_guardrail_down):
        down = client.patch("/api/v1/chat/memories/m1", json={"content": "fine"})
    assert down.status_code == 503, down.text
    # Only the allowed edit reached the database.
    assert len(_updates(db)) == 1
