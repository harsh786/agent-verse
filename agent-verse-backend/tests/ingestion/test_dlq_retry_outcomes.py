"""DLQ retry outcomes (``ingestion.retry_dlq_entries``).

* a ``dedup`` skip means the content IS indexed → the entry resolves (it used to
  be counted as another failed attempt until it went permanent);
* an entry whose Source was deleted can never be replayed → permanent at once
  (it used to call the pipeline with ``source_config=None`` five more times);
* a real failure increments the attempt (which schedules backoff).
"""

from __future__ import annotations

import json
from typing import Any
from unittest.mock import patch

from app.ingestion.scheduler import _retry_dlq_async
from app.ingestion.source_config import PipelineResult, SourceConfig, SourceFamily


class _Tracker:
    def __init__(self, entries: list[dict[str, Any]]) -> None:
        self.entries = entries
        self.resolved: list[str] = []
        self.permanent: list[str] = []
        self.retried: list[tuple[str, str]] = []

    async def get_retryable_dlq_entries(self, max_entries: int = 50) -> list[dict[str, Any]]:
        return self.entries[:max_entries]

    async def resolve_dlq_entry(self, dlq_id: str, tenant_id: str) -> None:
        self.resolved.append(dlq_id)

    async def mark_dlq_permanent_failure(self, dlq_id: str, tenant_id: str) -> None:
        self.permanent.append(dlq_id)

    async def increment_dlq_retry(self, dlq_id: str, tenant_id: str, error: str = "") -> None:
        self.retried.append((dlq_id, error))


class _Store:
    def __init__(self, known: set[str]) -> None:
        self.known = known

    async def get(self, source_id: str, tenant_id: str) -> SourceConfig | None:
        if source_id not in self.known:
            return None
        return SourceConfig(
            source_id=source_id, tenant_id=tenant_id, name="s",
            family=SourceFamily.WEB, source_type="http",
        )


class _Pipeline:
    def __init__(self, outcomes: dict[str, tuple[str, str]]) -> None:
        self.outcomes = outcomes

    async def run(self, raw_doc: Any, *, source_config: Any = None) -> PipelineResult:
        status, reason = self.outcomes[raw_doc.doc_id]
        return PipelineResult(
            doc_id=raw_doc.doc_id, source_id=raw_doc.source_id, tenant_id=raw_doc.tenant_id,
            status=status, skip_reason=reason, error="boom" if status == "failed" else "",
        )


def _entry(dlq_id: str, source_id: str, doc_id: str) -> dict[str, Any]:
    return {
        "dlq_id": dlq_id, "tenant_id": "t1", "source_id": source_id, "doc_id": doc_id,
        "retry_count": 0,
        "raw_doc_json": json.dumps({"doc_id": doc_id, "content": "hello", "content_type": "x"}),
    }


async def test_retry_outcomes() -> None:
    tracker = _Tracker(
        [
            _entry("q-dedup", "src", "d-dedup"),
            _entry("q-gone", "deleted-src", "d-gone"),
            _entry("q-fail", "src", "d-fail"),
            _entry("q-ok", "src", "d-ok"),
        ]
    )
    pipeline = _Pipeline(
        {
            "d-dedup": ("skipped", "dedup"),
            "d-fail": ("failed", ""),
            "d-ok": ("indexed", ""),
        }
    )
    with patch(
        "app.ingestion.scheduler._build_worker_ingestion",
        return_value=(tracker, pipeline, _Store({"src"})),
    ):
        result = await _retry_dlq_async()
    assert sorted(tracker.resolved) == ["q-dedup", "q-ok"]
    assert tracker.permanent == ["q-gone"]
    assert tracker.retried == [("q-fail", "boom")]
    assert result == {"retried": 4, "succeeded": 2, "still_failed": 2}
