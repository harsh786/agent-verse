"""MEM-36: execution-memory recall selects by relevance IN SQL.

Recall read the newest few rows and keyword-filtered them in Python, so an
active tenant's relevant older plans and failures were never recalled. The
fake DB below answers only a query that carries a relevance predicate.
"""

from __future__ import annotations

from typing import Any

from app.memory.execution import ExecutionMemory
from tests._rls_recorder import RlsRecordingDb

T = "t-exec-rel"


def _relevance_only(rows: list[Any]) -> Any:
    def _rows(sql: str, params: dict[str, Any]) -> list[Any]:
        if "FROM execution_memory" not in sql:
            return []
        if "word_similarity" not in sql or ":q" not in sql or params.get("q") is None:
            return []  # a recency-only window: what the old code asked for
        return rows

    return _rows


async def test_recall_plans_asks_sql_for_relevant_rows() -> None:
    db = RlsRecordingDb(
        rows_for=_relevance_only([("rotate the TLS certificates", ["renew", "deploy"], True)])
    )
    hits = await ExecutionMemory().recall_async("rotate TLS certs", tenant_id=T, db=db)
    assert [h["goal"] for h in hits] == ["rotate the TLS certificates"]
    (stmt,) = db.touching("FROM execution_memory")
    assert stmt.tenant_guc == T and "success = TRUE" in stmt.sql
    assert "LIMIT :lim" in stmt.sql and stmt.params["lim"] == 3


async def test_recall_failures_asks_sql_for_relevant_rows() -> None:
    db = RlsRecordingDb(
        rows_for=_relevance_only([("rotate the TLS certificates", {"error": "expired"})])
    )
    hits = await ExecutionMemory().recall_failures_async("rotate TLS", tenant_id=T, db=db)
    assert hits == [
        {"goal": "rotate the TLS certificates", "goal_text": "rotate the TLS certificates",
         "error": "expired"}
    ]
    (stmt,) = db.touching("FROM execution_memory")
    assert stmt.tenant_guc == T and "success = FALSE" in stmt.sql


async def test_blank_hint_reads_the_recent_window() -> None:
    db = RlsRecordingDb(rows_for=lambda _s, _p: [("anything", [], True)])
    hits = await ExecutionMemory().recall_async("   ", tenant_id=T, db=db)
    assert len(hits) == 1
