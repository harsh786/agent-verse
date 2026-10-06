"""NF-18: every path that pulls a connector's delta goes through the shared lock.

TG-12 made the per-Source sync lock a shared Redis lock in
``IngestionJobTracker`` (held + renewed via ``SyncLease``, fenced cursor
commits). Two paths still ran a connector's ``get_delta`` without it:

* ``app.api.ingestion._run_sync`` — an in-process sync left over from before
  manual syncs moved to Celery: no lease, no fence, and it advanced the durable
  cursor itself (dead code, removed);
* ``delta_reingest_files`` — the webhook re-ingest task (GitHub / Confluence /
  Notion pushes, any connector type): a burst of webhooks ran full re-reads of
  the same source concurrently, on every worker.
"""

from __future__ import annotations

import ast
import asyncio
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import patch

from app.ingestion.job_tracker import IngestionJobTracker, SyncLockUnavailableError

APP = Path(__file__).resolve().parents[2] / "app"

# (module, enclosing function) -> why it may call get_delta
_ALLOWED = {
    ("app/ingestion/scheduler.py", "_sync_locked"): "runs under tracker.hold (lease + fence)",
    ("app/api/ingestion.py", "preview_source"): "dry run: nothing indexed, no cursor",
    ("app/scaling/tasks.py", "_run"): "delta_reingest_files, under the shared lock",
    ("app/ingestion/legacy_source_jobs.py", "run_legacy_source_ingest"): (
        "one-shot legacy ingest job (a04-F067-01): no Source and no cursor; exclusive "
        "through the ingestion_jobs lease (claim + per-document heartbeat, aborts when "
        "the lease is lost), so a redelivered task cannot run it twice"
    ),
}


def _get_delta_callers() -> set[tuple[str, str]]:
    found: set[tuple[str, str]] = set()
    for path in APP.rglob("*.py"):
        rel = path.relative_to(APP.parent).as_posix()
        if rel.startswith("app/ingestion/connectors/") or rel == "app/ingestion/base_connector.py":
            continue  # connectors define / compose get_delta themselves
        tree = ast.parse(path.read_text())
        for fn in ast.walk(tree):
            if not isinstance(fn, ast.FunctionDef | ast.AsyncFunctionDef):
                continue
            for node in ast.walk(fn):
                if (
                    isinstance(node, ast.Call)
                    and isinstance(node.func, ast.Attribute)
                    and node.func.attr == "get_delta"
                ):
                    found.add((rel, fn.name))
    # Keep only the innermost function names that actually contain the call.
    return found


def test_every_get_delta_caller_is_a_locked_or_dry_run_path() -> None:
    callers = _get_delta_callers()
    # an outer function also "contains" the call through its nested function
    callers -= {("app/scaling/tasks.py", "delta_reingest_files")}
    unexpected = callers - set(_ALLOWED)
    assert not unexpected, f"get_delta called outside the shared sync lock: {unexpected}"


def test_the_unlocked_in_process_sync_is_gone() -> None:
    import app.api.ingestion as ingestion_api

    assert not hasattr(ingestion_api, "_run_sync")


# ── delta_reingest_files ──────────────────────────────────────────────────────


class _Docs:
    calls = 0
    tracker: Any = None
    seen_lock: list[str | None] = []

    async def get_delta(self, config: Any, cursor: Any):  # type: ignore[no-untyped-def]
        type(self).calls += 1
        for i in range(3):
            if type(self).tracker is not None:
                type(self).seen_lock.append(
                    await type(self).tracker.running_job_id(config.source_id, config.tenant_id)
                )
            yield SimpleNamespace(doc_id=f"d{i}"), f"c{i}"


class _Pipeline:
    def __init__(self) -> None:
        self.docs: list[str] = []

    async def ingest(self, raw_doc: Any, config: Any) -> Any:
        self.docs.append(raw_doc.doc_id)
        return SimpleNamespace(success=True, skipped=False)


def _run(tracker: Any, pipeline: Any) -> dict[str, Any]:
    from app.scaling.tasks import delta_reingest_files

    _Docs.calls = 0
    _Docs.seen_lock = []
    with (
        patch(
            "app.ingestion.scheduler._build_worker_ingestion",
            return_value=(tracker, pipeline, SimpleNamespace()),
        ),
        patch("app.ingestion.connector_registry.get_connector", return_value=_Docs),
        patch("app.ingestion.connector_registry.load_all_connectors", return_value=None),
    ):
        return dict(
            delta_reingest_files.run(
                tenant_id="t-nf18", collection_id="c1", source_type="notion", source_config={}
            )
        )


def test_webhook_reingest_holds_the_shared_lock_and_releases_it() -> None:
    tracker = IngestionJobTracker()
    _Docs.tracker = tracker
    pipeline = _Pipeline()
    try:
        out = _run(tracker, pipeline)
    finally:
        _Docs.tracker = None

    assert out["status"] == "ok"
    assert pipeline.docs == ["d0", "d1", "d2"]
    assert all(token is not None for token in _Docs.seen_lock)  # held while ingesting
    assert asyncio.run(tracker.running_job_id("webhook-notion", "t-nf18")) is None


def test_webhook_reingest_skips_while_another_run_holds_the_lock() -> None:
    tracker = IngestionJobTracker()
    holder = asyncio.run(tracker.acquire_lock("webhook-notion", "t-nf18"))
    assert holder
    pipeline = _Pipeline()

    out = _run(tracker, pipeline)

    assert out["status"] == "skipped"
    assert out["reason"] == "already_running"
    assert _Docs.calls == 0 and pipeline.docs == []
    # The other run's lock is untouched.
    assert asyncio.run(tracker.running_job_id("webhook-notion", "t-nf18")) == holder


def test_webhook_reingest_fails_closed_when_the_lock_cannot_be_checked() -> None:
    tracker = IngestionJobTracker()

    async def _down(*a: Any, **k: Any) -> Any:
        raise SyncLockUnavailableError("redis down")

    tracker.acquire_lock = _down  # type: ignore[method-assign]
    pipeline = _Pipeline()

    out = _run(tracker, pipeline)

    assert out["status"] == "error"
    assert "lock" in out["error"]
    assert _Docs.calls == 0 and pipeline.docs == []


def test_webhook_reingest_stops_when_its_lease_is_lost() -> None:
    tracker = IngestionJobTracker()

    class _LosingPipeline(_Pipeline):
        async def ingest(self, raw_doc: Any, config: Any) -> Any:
            if raw_doc.doc_id == "d0":
                # Another run took the lock (e.g. TTL expired while stalled).
                tracker._locks["webhook-notion"] = "someone-else"
            await asyncio.sleep(0.2)  # let the lease renewal notice
            return await super().ingest(raw_doc, config)

    pipeline = _LosingPipeline()
    with patch("app.scaling.tasks._webhook_reingest_lock_ttl", return_value=0.3):
        out = _run(tracker, pipeline)

    assert out["status"] == "error"
    assert out["reason"] == "lock_lost"
    assert pipeline.docs != ["d0", "d1", "d2"]
    # The other run's lock is not released by the stopped run.
    assert tracker._locks.get("webhook-notion") == "someone-else"

