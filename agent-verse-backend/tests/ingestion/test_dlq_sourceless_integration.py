"""Source-less ingestion DLQ entries on a real schema (migration c3d5e7f9a1b2).

A failed single-URL ingest (and a repository ingest) has no ``source_configs``
row. Before, ``ingestion_dlq.source_id`` was NOT NULL with an FK to
``source_configs``, so these entries could never be written. Now: written with
``source_id`` NULL under the NOBYPASSRLS app role, one OPEN entry per
(tenant, doc_id) (the same failure again refreshes it), found by the retry scan,
resolved by a later success, and invisible to another tenant.

    DOCKER_HOST=unix:///Users/harsh/.colima/default/docker.sock \\
    TESTCONTAINERS_RYUK_DISABLED=true \\
        uv run pytest tests/ingestion/test_dlq_sourceless_integration.py -m integration
"""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy.ext.asyncio import create_async_engine

from app.ingestion.job_tracker import IngestionJobTracker
from tests.rag._pg import admin_exec, app_engine, seed_tenant, sessions

pytestmark = pytest.mark.integration


async def test_sourceless_dlq_entry_is_idempotent_retryable_and_resolvable(pg_url: str) -> None:
    tenant_a = f"t-dlq-{uuid.uuid4().hex[:8]}"
    tenant_b = f"t-dlq-{uuid.uuid4().hex[:8]}"
    for tid in (tenant_a, tenant_b):
        await seed_tenant(pg_url, tid)
    app = await app_engine(pg_url)
    maint = create_async_engine(pg_url)
    try:
        tracker = IngestionJobTracker(db=sessions(app), system_db=sessions(maint))
        doc_id = uuid.uuid4().hex
        payload = {"kind": "url", "url": "https://example.test/p", "collection_id": "c1"}

        first = await tracker.dead_letter_sourceless(
            tenant_id=tenant_a, doc_id=doc_id, error="504: timeout", payload=payload,
            failed_stage="url_ingest", failure_type="url_ingest_failure",
        )
        assert first is not None
        # The same failure again: the open entry is refreshed, not duplicated.
        again = await tracker.dead_letter_sourceless(
            tenant_id=tenant_a, doc_id=doc_id, error="502: refused", payload=payload,
            failed_stage="url_ingest", failure_type="url_ingest_failure",
        )
        assert again == first
        rows = await admin_exec(
            pg_url,
            "SELECT id, source_id, error_message, raw_doc_json FROM ingestion_dlq "
            "WHERE tenant_id = :t",
            {"t": tenant_a},
        )
        assert len(rows) == 1
        assert rows[0][0] == first and rows[0][1] is None
        assert rows[0][2] == "502: refused"
        assert '"kind": "url"' in rows[0][3]

        listed = await tracker.list_dlq_entries(tenant_a)
        assert [e["id"] for e in listed] == [first]
        assert await tracker.list_dlq_entries(tenant_b) == []

        scan = [e for e in await tracker.get_retryable_dlq_entries() if e["dlq_id"] == first]
        assert len(scan) == 1 and scan[0]["source_id"] is None

        # Another tenant can't resolve it; a success of the same URL does.
        await tracker.resolve_sourceless_dlq(tenant_id=tenant_b, doc_id=doc_id)
        assert (await tracker.list_dlq_entries(tenant_a))[0]["resolved_at"] is None
        await tracker.resolve_sourceless_dlq(tenant_id=tenant_a, doc_id=doc_id)
        assert await tracker.list_dlq_entries(tenant_a) == []

        # A new failure after the resolution opens a NEW entry.
        third = await tracker.dead_letter_sourceless(
            tenant_id=tenant_a, doc_id=doc_id, error="503", payload=payload
        )
        assert third is not None and third != first

        # The repository ingest's source-less entry is written too (its invented
        # ``repo:<url>`` source id used to violate the FK).
        repo_entry = await tracker.dead_letter_sourceless(
            tenant_id=tenant_a, doc_id=uuid.uuid4().hex, error="clone failed",
            payload={"kind": "repository", "repo_url": "https://github.com/x/" + "y" * 80},
        )
        assert repo_entry is not None
    finally:
        await app.dispose()
        await maint.dispose()
