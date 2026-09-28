"""Integration: ingestion under the production database posture.

Production runs the API — and the Celery worker's per-tenant work — as a
NOSUPERUSER, NOBYPASSRLS role, and the cross-tenant beat scans as a separate
BYPASSRLS maintenance role. ``source_configs``, ``ingestion_jobs`` and
``ingestion_dlq`` are all FORCE ROW LEVEL SECURITY, so under those roles:

* a statement with no ``app.tenant_id`` GUC is rejected (INSERT) or matches
  nothing (UPDATE/SELECT) — which is what happened to every ingestion job row,
  DLQ insert and failure counter, silently, because the errors were swallowed;
* ``system_session`` on the application role makes every statement fail
  ("query would be affected by row-level security") — which is what broke the
  beat due-scan, the worker's Source load and the DLQ retry scan.

This drives the real stores/tracker/sync bodies against a real schema
(``alembic upgrade head``) through exactly those two roles.

Run with::

    DOCKER_HOST=unix:///Users/harsh/.colima/default/docker.sock \\
    TESTCONTAINERS_RYUK_DISABLED=true \\
        uv run pytest tests/ingestion/test_ingestion_rls_integration.py -q -m integration
"""

from __future__ import annotations

import os
import secrets
import subprocess
from collections.abc import AsyncIterator, Iterator
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import MagicMock, patch

import pytest
import pytest_asyncio
from sqlalchemy import text
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from testcontainers.postgres import PostgresContainer  # type: ignore[import-untyped]

from app.api.ingestion import _run_sync
from app.ingestion.job_tracker import IngestionJobTracker
from app.ingestion.scheduler import _retry_dlq_async, _sync_source_async
from app.ingestion.source_config import PipelineResult, RawDocument, SourceConfig, SourceFamily
from app.ingestion.source_store import SourceConfigStore

pytestmark = pytest.mark.integration

BACKEND_ROOT = Path(__file__).resolve().parents[2]
TENANT_A = "tenant-ing-a"
TENANT_B = "tenant-ing-b"
_TABLES = ("source_configs", "ingestion_jobs", "ingestion_dlq")


@pytest.fixture(scope="module")
def postgres_url() -> Iterator[str]:
    with PostgresContainer("pgvector/pgvector:pg16", driver="asyncpg") as postgres:
        admin_url = postgres.get_connection_url()
        subprocess.run(
            ["alembic", "upgrade", "head"],
            cwd=BACKEND_ROOT,
            env={**os.environ, "DATABASE_URL": admin_url},
            check=True,
            capture_output=True,
            text=True,
        )
        yield admin_url


async def _create_role(conn: Any, role: str, password: str, *, bypass_rls: bool) -> None:
    quoted = (await conn.execute(text("SELECT quote_literal(:p)"), {"p": password})).scalar_one()
    await conn.execute(
        text(
            f"CREATE ROLE {role} LOGIN PASSWORD {quoted} NOSUPERUSER NOCREATEDB NOCREATEROLE "
            + ("BYPASSRLS" if bypass_rls else "NOBYPASSRLS")
        )
    )
    await conn.execute(text(f"GRANT CONNECT ON DATABASE test TO {role}"))
    await conn.execute(text(f"GRANT USAGE ON SCHEMA public TO {role}"))
    await conn.execute(
        text(f"GRANT SELECT, INSERT, UPDATE, DELETE ON {', '.join(_TABLES)} TO {role}")
    )


@pytest_asyncio.fixture(scope="function")
async def dbs(postgres_url: str) -> AsyncIterator[SimpleNamespace]:
    """admin (superuser; setup + verification), app (NOBYPASSRLS, the API and the
    worker's per-tenant work) and maint (BYPASSRLS, non-superuser: beat scans)."""
    suffix = secrets.token_hex(4)
    app_role, maint_role = f"ing_app_{suffix}", f"ing_maint_{suffix}"
    app_pw, maint_pw = secrets.token_urlsafe(24), secrets.token_urlsafe(24)
    admin_engine = create_async_engine(postgres_url)
    async with admin_engine.begin() as conn:
        await _create_role(conn, app_role, app_pw, bypass_rls=False)
        await _create_role(conn, maint_role, maint_pw, bypass_rls=True)
        for table in ("ingestion_dlq", "ingestion_jobs", "source_configs"):
            await conn.execute(text(f"DELETE FROM {table}"))

    def _url(role: str, password: str) -> str:
        return (
            make_url(postgres_url)
            .set(username=role, password=password)
            .render_as_string(hide_password=False)
        )

    app_engine = create_async_engine(_url(app_role, app_pw), pool_size=4, max_overflow=0)
    maint_engine = create_async_engine(_url(maint_role, maint_pw), pool_size=2, max_overflow=0)
    yield SimpleNamespace(
        admin=async_sessionmaker(admin_engine, expire_on_commit=False),
        app=async_sessionmaker(app_engine, expire_on_commit=False),
        maint=async_sessionmaker(maint_engine, expire_on_commit=False),
    )
    await app_engine.dispose()
    await maint_engine.dispose()
    await admin_engine.dispose()


def _source(tenant_id: str, source_id: str, **overrides: Any) -> SourceConfig:
    fields: dict[str, Any] = dict(
        source_id=source_id,
        tenant_id=tenant_id,
        name=f"src {source_id}",
        family=SourceFamily.WEB,
        source_type="http",
    )
    fields.update(overrides)
    return SourceConfig(**fields)


def _raw(doc_id: str, source_id: str, tenant_id: str) -> RawDocument:
    return RawDocument(
        doc_id=doc_id,
        source_id=source_id,
        tenant_id=tenant_id,
        content=b"bytes \x00\xff",
        content_type="text/plain",
    )


async def _row(admin: Any, sql: str, **params: Any) -> Any:
    async with admin() as s:
        return (await s.execute(text(sql), params)).mappings().first()


# ── /sources* API store: tenant RLS ──────────────────────────────────────────


@pytest.mark.asyncio
async def test_source_crud_is_tenant_isolated_under_the_app_role(dbs: SimpleNamespace) -> None:
    store = SourceConfigStore(db=dbs.app)
    await store.create(_source(TENANT_A, "src-a"))
    await store.create(_source(TENANT_B, "src-b"))

    assert [s.source_id for s in await store.list(TENANT_A)] == ["src-a"]
    assert await store.get("src-a", TENANT_A) is not None
    assert await store.get("src-b", TENANT_A) is None
    assert await store.update("src-b", TENANT_A, name="hijacked") is None
    assert await store.delete("src-b", TENANT_A) is False

    row = await _row(dbs.admin, "SELECT name FROM source_configs WHERE id = 'src-b'")
    assert row["name"] == "src src-b"


# ── manual sync (POST /sources/{id}/sync, POST /knowledge/collections/{id}/sync) ─


class _FakePipeline:
    def __init__(self, statuses: list[str]) -> None:
        self._statuses = list(statuses)

    async def ingest(self, raw_doc: Any, src: SourceConfig) -> Any:
        return SimpleNamespace(
            status=self._statuses.pop(0), chunks_created=2, tokens_consumed=3
        )


def _connector_cls(items: list[tuple[Any, str]]) -> Any:
    class _Connector:
        async def get_delta(self, src: SourceConfig, cursor: str | None) -> Any:
            for raw_doc, new_cursor in items:
                yield raw_doc, new_cursor

    return _Connector


@pytest.mark.asyncio
async def test_manual_sync_persists_job_cursor_and_stats_under_the_app_role(
    dbs: SimpleNamespace,
) -> None:
    store = SourceConfigStore(db=dbs.app)
    tracker = IngestionJobTracker(db=dbs.app)
    await store.create(_source(TENANT_A, "src-sync"))
    source = await store.get("src-sync", TENANT_A)
    assert source is not None
    job_id = await tracker.acquire_lock("src-sync", TENANT_A)
    assert job_id is not None

    connector = _connector_cls([("d1", "c1"), ("d2", "c2")])
    with patch("app.ingestion.connector_registry.get_connector", return_value=connector):
        await _run_sync(source, _FakePipeline(["indexed", "failed"]), tracker, job_id, store)

    job = await _row(dbs.admin, "SELECT * FROM ingestion_jobs WHERE id = :id", id=job_id)
    assert job is not None, "job row never persisted (INSERT rejected by RLS)"
    assert job["tenant_id"] == TENANT_A
    assert job["status"] == "completed"
    assert job["triggered_by"] == "manual"
    assert (job["docs_indexed"], job["docs_failed"], job["chunks_created"]) == (1, 1, 4)
    assert job["cursor_after"] == "c2"

    src = await _row(dbs.admin, "SELECT * FROM source_configs WHERE id = 'src-sync'")
    assert src["last_synced_at"] is not None
    assert src["cursor_value"] == "c2"
    assert src["total_docs_indexed"] == 1
    assert src["consecutive_failures"] == 1  # a doc failed → mark_synced counts it
    assert tracker._locks.get("src-sync") is None


@pytest.mark.asyncio
async def test_tracker_cannot_touch_another_tenants_job(dbs: SimpleNamespace) -> None:
    store = SourceConfigStore(db=dbs.app)
    tracker = IngestionJobTracker(db=dbs.app)
    await store.create(_source(TENANT_B, "src-b-job"))
    job = await tracker.create_job(_source(TENANT_B, "src-b-job"), job_id="job-b")

    # Same job id, forged tenant: the UPDATE runs under tenant A's GUC and
    # tenant predicate, so it cannot see tenant B's row.
    job.tenant_id = TENANT_A
    await tracker.complete_job(job, error="forged")

    row = await _row(dbs.admin, "SELECT status, tenant_id FROM ingestion_jobs WHERE id = 'job-b'")
    assert (row["status"], row["tenant_id"]) == ("running", TENANT_B)


# ── beat due-scan: maintenance role ──────────────────────────────────────────


@pytest.mark.asyncio
async def test_due_scan_needs_the_maintenance_role(dbs: SimpleNamespace) -> None:
    store = SourceConfigStore(db=dbs.app)
    await store.create(_source(TENANT_A, "due-a"))
    await store.create(_source(TENANT_B, "due-b"))
    await store.create(_source(TENANT_B, "off-b", enabled=False))

    # system_session on the application (NOBYPASSRLS) role fails loudly — the
    # exact failure the beat dispatcher hit before it moved to the system factory.
    with pytest.raises(Exception, match="row-level security"):
        await SourceConfigStore(system_db=dbs.app).list_due()

    due = await SourceConfigStore(system_db=dbs.maint).list_due()
    assert set(due) == {("due-a", TENANT_A), ("due-b", TENANT_B)}
    # The tracker's own due-scan delegates to the same maintenance-role scan.
    tracker = IngestionJobTracker(db=dbs.app, system_db=dbs.maint)
    assert set(await tracker.get_due_sources()) == set(due)


# ── scheduled sync worker: per-tenant RLS ────────────────────────────────────


@pytest.mark.asyncio
async def test_scheduled_sync_runs_under_tenant_rls(dbs: SimpleNamespace) -> None:
    store = SourceConfigStore(db=dbs.app)
    await store.create(_source(TENANT_A, "src-worker"))
    tracker = IngestionJobTracker(db=dbs.app, system_db=dbs.maint)

    pipeline = MagicMock()
    results = iter(
        [
            PipelineResult(doc_id="w1", source_id="src-worker", tenant_id=TENANT_A, status="indexed"),
            PipelineResult(
                doc_id="w2", source_id="src-worker", tenant_id=TENANT_A, status="failed", error="bad"
            ),
        ]
    )

    async def _ingest(raw_doc: Any, cfg: SourceConfig) -> PipelineResult:
        return next(results)

    pipeline.ingest = _ingest
    connector = _connector_cls(
        [(_raw("w1", "src-worker", TENANT_A), "k1"), (_raw("w2", "src-worker", TENANT_A), "k2")]
    )
    with (
        patch(
            "app.ingestion.scheduler._build_worker_ingestion",
            return_value=(tracker, pipeline, store),
        ),
        patch("app.ingestion.connector_registry.load_all_connectors"),
        patch("app.ingestion.connector_registry.get_connector", return_value=connector),
    ):
        result = await _sync_source_async(
            task=MagicMock(), source_id="src-worker", tenant_id=TENANT_A, triggered_by="scheduler"
        )

    assert result["docs_indexed"] == 1 and result["docs_failed"] == 1, result
    job = await _row(dbs.admin, "SELECT * FROM ingestion_jobs WHERE id = :id", id=result["job_id"])
    assert job is not None and job["status"] == "completed" and job["cursor_after"] == "k2"
    dlq = await _row(dbs.admin, "SELECT * FROM ingestion_dlq WHERE source_id = 'src-worker'")
    assert dlq is not None, "DLQ INSERT never landed"
    assert (dlq["tenant_id"], dlq["doc_id"], dlq["job_id"]) == (TENANT_A, "w2", result["job_id"])
    assert dlq["id"] == dlq["dlq_id"]
    src = await _row(dbs.admin, "SELECT * FROM source_configs WHERE id = 'src-worker'")
    assert src["last_synced_at"] is not None and src["cursor_value"] == "k2"


# ── DLQ retry: maintenance-role scan, per-tenant follow-ups ─────────────────


@pytest.mark.asyncio
async def test_dlq_retry_scans_as_maintenance_and_acts_per_tenant(dbs: SimpleNamespace) -> None:
    store = SourceConfigStore(db=dbs.app)
    app_tracker = IngestionJobTracker(db=dbs.app, system_db=dbs.maint)
    for tenant, sid in ((TENANT_A, "dlq-a"), (TENANT_B, "dlq-b")):
        await store.create(_source(tenant, sid))
        await app_tracker.add_to_dlq(
            source_id=sid, tenant_id=tenant, doc_id=f"doc-{sid}", error="e",
            raw_doc=_raw(f"doc-{sid}", sid, tenant),
        )
    # One entry already at the retry cap.
    await store.create(_source(TENANT_A, "dlq-cap"))
    await app_tracker.add_to_dlq(
        source_id="dlq-cap", tenant_id=TENANT_A, doc_id="doc-cap", error="e",
        raw_doc=_raw("doc-cap", "dlq-cap", TENANT_A),
    )
    async with dbs.admin() as s, s.begin():
        await s.execute(text("UPDATE ingestion_dlq SET retry_count = 5 WHERE source_id = 'dlq-cap'"))

    # On the application role the cross-tenant scan cannot run.
    assert await IngestionJobTracker(db=dbs.app, system_db=dbs.app).get_retryable_dlq_entries() == []
    entries = await app_tracker.get_retryable_dlq_entries()
    assert {e["source_id"] for e in entries} == {"dlq-a", "dlq-b", "dlq-cap"}

    replayed: list[RawDocument] = []

    class _Pipeline:
        async def run(self, raw_doc: RawDocument, *, source_config: Any = None) -> PipelineResult:
            assert source_config is not None and source_config.tenant_id == raw_doc.tenant_id
            replayed.append(raw_doc)
            return PipelineResult(
                doc_id=raw_doc.doc_id, source_id=raw_doc.source_id,
                tenant_id=raw_doc.tenant_id, status="indexed",
            )

    with patch(
        "app.ingestion.scheduler._build_worker_ingestion",
        return_value=(app_tracker, _Pipeline(), store),
    ):
        result = await _retry_dlq_async()

    assert result == {"retried": 2, "succeeded": 2, "still_failed": 0}
    assert {(d.tenant_id, d.doc_id) for d in replayed} == {
        (TENANT_A, "doc-dlq-a"), (TENANT_B, "doc-dlq-b"),
    }
    assert all(d.content == b"bytes \x00\xff" for d in replayed)
    async with dbs.admin() as s:
        rows = {
            r["source_id"]: r
            for r in (
                await s.execute(
                    text("SELECT source_id, resolved_at, permanent_failure FROM ingestion_dlq")
                )
            ).mappings()
        }
    assert rows["dlq-a"]["resolved_at"] is not None
    assert rows["dlq-b"]["resolved_at"] is not None  # tenant B's row, under B's context
    assert rows["dlq-cap"]["permanent_failure"] is True
    assert rows["dlq-cap"]["resolved_at"] is None
