"""LongTermMemoryStore CRUD runs under the tenant RLS GUC and fails loudly.

``long_term_memory`` is FORCE ROW LEVEL SECURITY and the API connects as a
NOBYPASSRLS role. ``list_all_async`` / ``delete_async`` / ``update_content_async``
/ ``delete_all_async`` ran without ``app.tenant_id`` set, so under that role the
list was always empty and ``DELETE /chat/memories`` (GDPR erasure) matched no
rows and returned ``{"deleted": 0}``. DB errors were also swallowed into fake
results (``0`` / ``False`` / cache), so a failed erasure looked like success.
"""
from __future__ import annotations

from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.memory.long_term import LongTermMemoryStore, LongTermMemoryUnavailableError
from app.tenancy.context import PlanTier, TenantContext
from tests._rls_recorder import RlsRecordingDb, assert_tenant_scoped

TENANT = "tenant-ltm-a"


def _ctx() -> TenantContext:
    return TenantContext(tenant_id=TENANT, plan=PlanTier.PROFESSIONAL, api_key_id="k1")


def _store(db: Any) -> LongTermMemoryStore:
    s = LongTermMemoryStore()
    s._db_factory = db
    return s


async def test_list_reads_under_tenant_guc_and_uses_rows() -> None:
    rows = [("m1", "likes tea", "domain_fact", 0.9, "g1", ["x"])]
    db = RlsRecordingDb(rows_for=lambda sql, _p: rows if "FROM long_term_memory" in sql else [])
    mems = await _store(db).list_all_async(tenant_ctx=_ctx())
    assert_tenant_scoped(db, "long_term_memory", TENANT)
    assert [m.memory_id for m in mems] == ["m1"]
    assert mems[0].content == "likes tea"


async def test_delete_runs_under_tenant_guc() -> None:
    db = RlsRecordingDb(rows_for=lambda sql, _p: [1] if sql.startswith("DELETE") else [])
    assert await _store(db).delete_async(memory_id="m1", tenant_ctx=_ctx()) is True
    (stmt,) = assert_tenant_scoped(db, "long_term_memory", TENANT)
    assert stmt.explicit_txn


async def test_update_runs_under_tenant_guc() -> None:
    db = RlsRecordingDb(rows_for=lambda sql, _p: [1] if sql.startswith("UPDATE") else [])
    m = await _store(db).update_content_async(memory_id="m1", content="new", tenant_ctx=_ctx())
    assert m is not None and m.content == "new"
    assert_tenant_scoped(db, "long_term_memory", TENANT)


async def test_delete_all_runs_under_tenant_guc_and_counts_rows() -> None:
    db = RlsRecordingDb(rows_for=lambda sql, _p: [1, 1, 1] if sql.startswith("DELETE") else [])
    assert await _store(db).delete_all_async(tenant_ctx=_ctx()) == 3
    assert_tenant_scoped(db, "long_term_memory", TENANT)


class _BrokenDb:
    def __call__(self) -> Any:
        raise ConnectionError("db down")


@pytest.mark.parametrize(
    "call",
    [
        lambda s: s.list_all_async(tenant_ctx=_ctx()),
        lambda s: s.delete_async(memory_id="m1", tenant_ctx=_ctx()),
        lambda s: s.update_content_async(memory_id="m1", content="c", tenant_ctx=_ctx()),
        lambda s: s.delete_all_async(tenant_ctx=_ctx()),
    ],
)
async def test_db_failure_raises_instead_of_fake_result(call: Any) -> None:
    with pytest.raises(LongTermMemoryUnavailableError):
        await call(_store(_BrokenDb()))


def _chat_client(ltm: LongTermMemoryStore) -> TestClient:
    from fastapi import Request

    from app.chat.router import router

    app = FastAPI()

    @app.middleware("http")
    async def inject(request: Request, call_next: Any) -> Any:
        request.state.tenant = _ctx()
        request.app.state.long_term_memory = ltm
        return await call_next(request)

    app.include_router(router, prefix="/api/v1")
    return TestClient(app, raise_server_exceptions=False)


def test_gdpr_delete_all_returns_5xx_on_db_failure() -> None:
    client = _chat_client(_store(_BrokenDb()))
    resp = client.delete("/api/v1/chat/memories", headers={"X-Confirm-Gdpr-Delete": "yes"})
    assert resp.status_code == 503, resp.text


def test_list_memories_returns_5xx_on_db_failure() -> None:
    client = _chat_client(_store(_BrokenDb()))
    resp = client.get("/api/v1/chat/memories")
    assert resp.status_code == 503, resp.text
