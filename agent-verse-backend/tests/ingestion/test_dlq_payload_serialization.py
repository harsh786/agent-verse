"""IngestionJobTracker.add_to_dlq must persist a retryable payload.

Previously the DLQ row's raw_doc_json was hardcoded to {"doc_id": ...}, discarding
the caller's raw_doc — so a dead-lettered item carried nothing to retry with. It
now serializes the provided payload (dict / dataclass / pydantic / JSON-safe).
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.ingestion.job_tracker import IngestionJobTracker


def _capturing_db() -> tuple[Any, dict[str, Any]]:
    """A session factory that captures the params of the INSERT."""
    captured: dict[str, Any] = {}
    session = AsyncMock()

    async def _execute(_query: Any, params: dict[str, Any]) -> Any:
        captured.update(params)
        return MagicMock()

    session.execute = AsyncMock(side_effect=_execute)
    begin = AsyncMock()
    begin.__aenter__ = AsyncMock(return_value=None)
    begin.__aexit__ = AsyncMock(return_value=False)
    session.begin = MagicMock(return_value=begin)

    cm = AsyncMock()
    cm.__aenter__ = AsyncMock(return_value=session)
    cm.__aexit__ = AsyncMock(return_value=False)

    def _factory() -> Any:
        return cm

    return _factory, captured


@pytest.mark.asyncio
async def test_add_to_dlq_serializes_dict_payload() -> None:
    db, captured = _capturing_db()
    tracker = IngestionJobTracker(db=db)
    await tracker.add_to_dlq(
        source_id="repo:https://x/y",
        tenant_id="t1",
        doc_id="job-1",
        error="boom",
        raw_doc={"repo_url": "https://x/y", "branch": "main", "max_files": 10},
    )
    payload = json.loads(captured["raw_doc_json"])
    assert payload["repo_url"] == "https://x/y"
    assert payload["branch"] == "main"
    assert payload["max_files"] == 10
    assert captured["doc_id"] == "job-1"
    assert captured["error"] == "boom"


@pytest.mark.asyncio
async def test_add_to_dlq_serializes_dataclass_payload() -> None:
    @dataclass
    class Doc:
        doc_id: str
        title: str

    db, captured = _capturing_db()
    tracker = IngestionJobTracker(db=db)
    await tracker.add_to_dlq(
        source_id="s1", tenant_id="t1", doc_id="d1", error="e", raw_doc=Doc("d1", "Hello")
    )
    payload = json.loads(captured["raw_doc_json"])
    assert payload == {"doc_id": "d1", "title": "Hello"}


@pytest.mark.asyncio
async def test_add_to_dlq_insert_is_complete_and_tenant_scoped() -> None:
    """The INSERT must supply every NOT NULL column of ingestion_dlq and run with
    the row's tenant GUC set (FORCE RLS). It omitted ``id``, ``failed_stage`` and
    ``failure_type`` — so it could never succeed — and set no tenant context."""
    statements: list[tuple[str, dict[str, Any]]] = []
    session = AsyncMock()

    async def _execute(query: Any, params: dict[str, Any] | None = None) -> Any:
        statements.append((" ".join(str(query).split()), params or {}))
        return MagicMock()

    session.execute = AsyncMock(side_effect=_execute)
    session.begin = MagicMock(return_value=AsyncMock())
    cm = AsyncMock()
    cm.__aenter__ = AsyncMock(return_value=session)

    tracker = IngestionJobTracker(db=lambda: cm)
    await tracker.add_to_dlq(
        source_id="s1", tenant_id="t-a", doc_id="d1", error="e", raw_doc={}, job_id="job-9"
    )

    set_idx = next(
        i for i, (s, p) in enumerate(statements) if "set_config" in s and p == {"tid": "t-a"}
    )
    ins_idx = next(i for i, (s, _) in enumerate(statements) if "INSERT INTO ingestion_dlq" in s)
    assert set_idx < ins_idx
    sql, params = statements[ins_idx]
    for column in ("id", "failed_stage", "failure_type", "tenant_id", "job_id"):
        assert f" {column}," in sql or f"({column}," in sql
    assert params["dlq_id"]
    assert params["tenant_id"] == "t-a"
    assert params["job_id"] == "job-9"
    assert params["failed_stage"] == "pipeline"
    assert params["failure_type"] == "pipeline_failure"


@pytest.mark.asyncio
async def test_add_to_dlq_without_db_is_a_noop() -> None:
    await IngestionJobTracker().add_to_dlq(
        source_id="s1", tenant_id="t1", doc_id="d1", error="e", raw_doc={}
    )


@pytest.mark.asyncio
async def test_add_to_dlq_falls_back_for_unserializable() -> None:
    db, captured = _capturing_db()
    tracker = IngestionJobTracker(db=db)
    await tracker.add_to_dlq(
        source_id="s1", tenant_id="t1", doc_id="d9", error="e", raw_doc=object()
    )
    # Not a dict/dataclass/model → minimal fallback record, still valid JSON.
    payload = json.loads(captured["raw_doc_json"])
    assert payload == {"doc_id": "d9"}
