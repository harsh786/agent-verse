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
async def test_add_to_dlq_falls_back_for_unserializable() -> None:
    db, captured = _capturing_db()
    tracker = IngestionJobTracker(db=db)
    await tracker.add_to_dlq(
        source_id="s1", tenant_id="t1", doc_id="d9", error="e", raw_doc=object()
    )
    # Not a dict/dataclass/model → minimal fallback record, still valid JSON.
    payload = json.loads(captured["raw_doc_json"])
    assert payload == {"doc_id": "d9"}
