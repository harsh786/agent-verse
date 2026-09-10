"""The on-demand /ingest/repo background path must dead-letter on failure.

The scheduled connector-sync path already dead-letters failed work (durable DLQ
+ retry). The on-demand repository ingest only marked the job 'failed' with no
DLQ entry, so a transient clone/index failure was lost. It now records a DLQ
entry carrying the repo parameters needed to retry, without masking the original
failure.
"""
from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock

from app.api.knowledge import _ingest_repo_background
from app.tenancy.context import PlanTier, TenantContext

_CTX = TenantContext(tenant_id="tid-dlq", plan=PlanTier.PROFESSIONAL, api_key_id="kid-dlq")


def test_repo_ingest_failure_is_dead_lettered() -> None:
    async def _run() -> None:
        store = MagicMock()
        # Lease can't be claimed → deterministic terminal failure before any git.
        store.claim_ingestion_job_async = AsyncMock(return_value=False)
        store.fail_ingestion_job_async = AsyncMock()

        tracker = MagicMock()
        tracker.add_to_dlq = AsyncMock()

        await _ingest_repo_background(
            job_id="job-dlq-1",
            repo_url="https://github.com/example/repo",
            collection_id="coll-1",
            branch="main",
            file_patterns=["**/*.py"],
            max_files=10,
            store=store,
            embedder=None,
            tenant_ctx=_CTX,
            curl_resolve="example.com:443:203.0.113.10",  # skip DNS resolution
            job_tracker=tracker,
        )

        # Job was marked failed AND dead-lettered with the retry payload.
        store.fail_ingestion_job_async.assert_awaited()
        tracker.add_to_dlq.assert_awaited_once()
        kwargs = tracker.add_to_dlq.await_args.kwargs
        assert kwargs["doc_id"] == "job-dlq-1"
        assert kwargs["tenant_id"] == "tid-dlq"
        assert kwargs["source_id"] == "repo:https://github.com/example/repo"
        raw = kwargs["raw_doc"]
        assert raw["repo_url"] == "https://github.com/example/repo"
        assert raw["collection_id"] == "coll-1"
        assert raw["branch"] == "main"
        assert raw["max_files"] == 10

    asyncio.run(_run())


def test_repo_ingest_without_tracker_still_completes() -> None:
    """No tracker wired → failure path must not crash (DLQ is best-effort)."""

    async def _run() -> None:
        store = MagicMock()
        store.claim_ingestion_job_async = AsyncMock(return_value=False)
        store.fail_ingestion_job_async = AsyncMock()

        await _ingest_repo_background(
            job_id="job-dlq-2",
            repo_url="https://github.com/example/repo",
            collection_id="coll-1",
            branch="main",
            file_patterns=["**/*.py"],
            max_files=10,
            store=store,
            embedder=None,
            tenant_ctx=_CTX,
            curl_resolve="example.com:443:203.0.113.10",
            job_tracker=None,
        )
        store.fail_ingestion_job_async.assert_awaited()

    asyncio.run(_run())
