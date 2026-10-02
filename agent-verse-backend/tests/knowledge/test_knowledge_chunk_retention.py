"""Retention must expire the real knowledge chunks, not only the legacy table.

``expire_stale_documents`` deleted from ``documents`` — which KnowledgeStore never
writes — so chunks in ``knowledge_chunks_<dim>`` past their ``expires_at`` (and
the graph extracted from them) were never deleted at all.
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from typing import Any
from unittest.mock import patch

import pytest


class _Res:
    def __init__(self, rows: list[Any] | None = None, rowcount: int = 0) -> None:
        self._rows = rows or []
        self.rowcount = rowcount

    def fetchall(self) -> list[Any]:
        return self._rows

    def first(self) -> Any:
        return self._rows[0] if self._rows else None


class _Session:
    """Records statements; answers the retention queries from a tiny script."""

    def __init__(self, log: list[tuple[str, str, dict[str, Any]]], scans: list[Any]) -> None:
        self.log = log
        self.scans = scans
        self.tenant = ""

    @asynccontextmanager
    async def begin(self) -> Any:
        yield self

    async def flush(self) -> None:
        return None

    async def execute(self, stmt: Any, params: dict[str, Any] | None = None) -> _Res:
        sql = " ".join(str(stmt).split())
        p = dict(params or {})
        if "set_config('app.tenant_id'" in sql:
            self.tenant = p.get("tid", "")
        self.log.append((self.tenant, sql, p))
        if sql.startswith("SELECT DISTINCT tenant_id"):
            return _Res(self.scans.pop(0) if self.scans and "knowledge_chunks_768" in sql else [])
        if sql.startswith("DELETE FROM knowledge_chunks_768"):
            return _Res([(100, 0), (50, 1)])
        if sql.startswith("SELECT 1 FROM knowledge_chunks_768"):
            return _Res([])  # nothing left: the document is gone
        if sql.startswith("SELECT id FROM knowledge_nodes"):
            return _Res([(f"node-{p['tid']}",)])
        if sql.startswith("DELETE FROM knowledge_edges"):
            # One edge extracted FROM the removed chunks (provenance) and one
            # edge touching the orphaned node: two statements, one row each.
            return _Res(rowcount=1)
        if sql.startswith("DELETE FROM knowledge_nodes"):
            # KB-53: only nodes left with no mention are deleted (RETURNING id).
            return _Res([(f"node-{p['tid']}",)])
        if sql.startswith("DELETE FROM documents"):
            return _Res([])
        return _Res()


def _factory(log: list[Any], scans: list[Any]) -> Any:
    @asynccontextmanager
    async def _open() -> Any:
        yield _Session(log, scans)

    return _open


@pytest.mark.asyncio
async def test_expired_chunks_are_deleted_per_tenant_under_rls() -> None:
    from app.rag.retention import expire_knowledge_chunks

    system_log: list[Any] = []
    app_log: list[Any] = []
    scans = [[("t1", "c1", "d1"), ("t2", "c2", "d2")]]
    totals = await expire_knowledge_chunks(
        system_db=_factory(system_log, scans), app_db=_factory(app_log, [])
    )

    assert totals == {
        "knowledge_chunks_expired": 4,
        "knowledge_documents_expired": 2,
        "graph_nodes_deleted": 2,
        "graph_edges_deleted": 4,  # per tenant: 1 by provenance + 1 touching the orphan
    }
    # The cross-tenant scan ran on the maintenance session, bounded, index-shaped.
    scan_sql = [s for _t, s, _p in system_log if s.startswith("SELECT DISTINCT")]
    assert any("expires_at IS NOT NULL AND expires_at < now()" in s for s in scan_sql)
    assert all("LIMIT :lim" in s for s in scan_sql)
    assert any("row_security" in s for _t, s, _p in system_log)
    # Every delete ran under the owning tenant's RLS context AND predicate.
    deletes = [(t, p) for t, s, p in app_log if s.startswith("DELETE FROM knowledge_chunks_768")]
    assert sorted(t for t, _ in deletes) == ["t1", "t2"]
    assert all(t == p["tid"] for t, p in deletes)
    assert not any("row_security" in s for _t, s, _p in app_log)
    # Collection counters decremented exactly.
    upd = [p for _t, s, p in app_log if s.startswith("UPDATE knowledge_collections")]
    assert {(u["cid"], u["d_chunks"], u["d_docs"], u["d_bytes"]) for u in upd} == {
        ("c1", 2, 1, 150),
        ("c2", 2, 1, 150),
    }
    # Graph rows extracted from each document (source_id "<doc>:<chunk_idx>").
    kg = [p for _t, s, p in app_log if s.startswith("SELECT id FROM knowledge_nodes")]
    assert {"d1", "d1:0", "d1:1"} <= set(kg[0]["sids"]) or {"d2", "d2:0", "d2:1"} <= set(
        kg[0]["sids"]
    )


@pytest.mark.asyncio
async def test_expire_stale_documents_task_expires_knowledge_chunks() -> None:
    from app.scaling.tasks import _expire_stale_documents

    log: list[Any] = []
    scans = [[("t1", "c1", "d1")]]
    factory = _factory(log, scans)
    with (
        patch("app.db.session.get_system_session_factory", return_value=factory),
        patch("app.db.session.get_session_factory", return_value=factory),
    ):
        result = await _expire_stale_documents(90)
    assert result["status"] == "ok", result
    assert result["knowledge_chunks_expired"] == 2
    assert result["knowledge_documents_expired"] == 1
    assert any(s.startswith("DELETE FROM knowledge_chunks_768") for _t, s, _p in log)
