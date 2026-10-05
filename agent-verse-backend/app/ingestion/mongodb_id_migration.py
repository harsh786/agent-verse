"""D2 — one-time reindex of MongoDB documents still under pre-v8 (host-based) ids.

MongoDB document ids were ``uuid5("mongodb://{host}/{db}/{collection}/{str(_id)}")``
until TG-13 / TG-07 made them UUID v8 keyed by Source + collection + canonical
``_id``. Documents indexed before keep their v5 id: an updated one is indexed again
under its v8 id while the stale v5 copy stays (deletion reconciliation never
touches v5 ids, see ``MongoDBConnector.manages_doc_id``).

This migration walks one Source's indexed documents in ``document_id`` order
(keyset pages, ``idx_knowledge_chunks_<dim>_source_doc``), picks the ones whose
id is provably the legacy scheme (the v5 id recomputed from its stored
``source_url`` matches), re-reads them upstream and:

* writes the current document under its v8 id and removes the v5 copy in the
  same transaction (``IngestionPipeline.ingest(..., supersedes=v5)``), or just
  removes the v5 copy when the v8 copy (or the same content) is already indexed;
* removes the v5 copy when the document no longer exists upstream;
* keeps a document under legal hold (document, collection or tenant) untouched;
* keeps — and counts as ``skipped`` — a legacy document from another database
  or from a collection the Source no longer reads (never deleted on a guess).

A copy is deleted only after a confirmed write or a confirmed absence upstream;
an upstream read or legal-hold check that fails stops the run with the cursor
at the start of the page (fail closed).

The cursor and tallies persist in ``ingestion_doc_id_migrations`` after every
page, so a run is bounded (``max_documents`` scanned) and the next one resumes
on any worker. Every statement runs for one tenant under its RLS context.

Triggers: the first sync of a MongoDB Source after the upgrade runs it (and each
later sync continues an unfinished one), and the idempotent maintenance task
``ingestion.migrate_mongodb_doc_ids`` runs it for every Source that has not
completed it. A finished Source is a no-op; one that finished with held, skipped
or failed documents is given a fresh pass by the maintenance task.
"""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any, Protocol
from urllib.parse import unquote

_log = logging.getLogger(__name__)

MIGRATION = "mongodb_v8_doc_ids"
STATUS_RUNNING = "running"
STATUS_COMPLETED = "completed"
STATUS_COMPLETED_WITH_ISSUES = "completed_with_issues"

MODE_SYNC = "sync"
MODE_MAINTENANCE = "maintenance"

DEFAULT_PAGE_SIZE = 200
DEFAULT_MAX_DOCUMENTS = 5000


@dataclass(frozen=True)
class LegacyRef:
    document_id: str
    database: str
    collection: str
    key: str  # str(_id) as the legacy id hashed it


def legacy_ref(document_id: str, source_url: str) -> LegacyRef | None:
    """The legacy reference of ``document_id``, or None when it is not provably legacy.

    A legacy id is ``uuid5(NAMESPACE_URL, "mongodb://{host}/{db}/{coll}/{key}")``
    and its ``source_url`` was that URL with the key percent-quoted. The id is
    accepted only when recomputing it from the parsed URL gives it back, so a
    v8 id, another connector's id or a mangled URL is never treated as legacy.
    """
    try:
        parsed = uuid.UUID(str(document_id))
    except ValueError:
        return None
    if parsed.version != 5 or not source_url.startswith("mongodb://"):
        return None
    rest = source_url[len("mongodb://") :]
    host, _, rest = rest.partition("/")
    database, _, rest = rest.partition("/")
    collection, _, quoted = rest.rpartition("/")
    if not (host and database and collection and quoted):
        return None
    key = unquote(quoted)
    expected = uuid.uuid5(uuid.NAMESPACE_URL, f"mongodb://{host}/{database}/{collection}/{key}")
    if str(expected) != str(parsed):
        return None
    return LegacyRef(str(parsed), database, collection, key)


@dataclass
class MigrationState:
    tenant_id: str
    source_id: str
    status: str = STATUS_RUNNING
    cursor: str | None = None
    scanned: int = 0
    migrated: int = 0
    deleted: int = 0
    held: int = 0
    skipped: int = 0
    failed: int = 0
    last_error: str | None = None
    started_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    completed_at: datetime | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "cursor": self.cursor,
            "scanned": self.scanned,
            "migrated": self.migrated,
            "deleted": self.deleted,
            "held": self.held,
            "skipped": self.skipped,
            "failed": self.failed,
            "last_error": self.last_error,
        }


class MigrationStateStore:
    """Persisted cursor + tallies per (tenant, Source), under the tenant's RLS.

    Without a database (dev/tests) the state lives in this object only.
    """

    def __init__(self, db: Any = None) -> None:
        self._db = db
        self._mem: dict[tuple[str, str], MigrationState] = {}

    async def get(self, tenant_id: str, source_id: str) -> MigrationState | None:
        if self._db is None:
            found = self._mem.get((tenant_id, source_id))
            return None if found is None else MigrationState(**vars(found))
        from sqlalchemy import text

        from app.db.rls import sqlalchemy_rls_context

        async with (
            self._db() as session,
            session.begin(),
            sqlalchemy_rls_context(session, tenant_id),
        ):
            row = (
                await session.execute(
                    text(
                        "SELECT status, cursor, scanned, migrated, deleted, held, skipped, "
                        "failed, last_error, started_at, completed_at "
                        "FROM ingestion_doc_id_migrations "
                        "WHERE tenant_id = :tid AND source_id = :sid AND migration = :m"
                    ),
                    {"tid": tenant_id, "sid": source_id, "m": MIGRATION},
                )
            ).fetchone()
        if row is None:
            return None
        return MigrationState(
            tenant_id=tenant_id,
            source_id=source_id,
            status=str(row[0]),
            cursor=row[1],
            scanned=int(row[2]),
            migrated=int(row[3]),
            deleted=int(row[4]),
            held=int(row[5]),
            skipped=int(row[6]),
            failed=int(row[7]),
            last_error=row[8],
            started_at=row[9],
            completed_at=row[10],
        )

    async def save(self, state: MigrationState) -> None:
        if self._db is None:
            self._mem[(state.tenant_id, state.source_id)] = MigrationState(**vars(state))
            return
        from sqlalchemy import text

        from app.db.rls import sqlalchemy_rls_context

        async with (
            self._db() as session,
            session.begin(),
            sqlalchemy_rls_context(session, state.tenant_id),
        ):
            await session.execute(
                text(
                    """
                    INSERT INTO ingestion_doc_id_migrations
                        (tenant_id, source_id, migration, status, cursor, scanned, migrated,
                         deleted, held, skipped, failed, last_error, started_at, updated_at,
                         completed_at)
                    VALUES (:tid, :sid, :m, :status, :cursor, :scanned, :migrated, :deleted,
                            :held, :skipped, :failed, :err, :started, now(), :completed)
                    ON CONFLICT (tenant_id, source_id, migration) DO UPDATE SET
                        status = EXCLUDED.status, cursor = EXCLUDED.cursor,
                        scanned = EXCLUDED.scanned, migrated = EXCLUDED.migrated,
                        deleted = EXCLUDED.deleted, held = EXCLUDED.held,
                        skipped = EXCLUDED.skipped, failed = EXCLUDED.failed,
                        last_error = EXCLUDED.last_error, started_at = EXCLUDED.started_at,
                        updated_at = now(), completed_at = EXCLUDED.completed_at
                    """
                ),
                {
                    "tid": state.tenant_id,
                    "sid": state.source_id,
                    "m": MIGRATION,
                    "status": state.status,
                    "cursor": state.cursor,
                    "scanned": state.scanned,
                    "migrated": state.migrated,
                    "deleted": state.deleted,
                    "held": state.held,
                    "skipped": state.skipped,
                    "failed": state.failed,
                    "err": state.last_error,
                    "started": state.started_at,
                    "completed": state.completed_at,
                },
            )


class _LegacyConnector(Protocol):
    def legacy_scope(self, config: Any) -> tuple[str, list[str]]: ...

    async def read_legacy_documents(
        self, config: Any, refs: list[tuple[str, str]]
    ) -> dict[tuple[str, str], list[Any]]: ...


def supports_legacy_id_migration(connector: Any) -> bool:
    return callable(getattr(connector, "read_legacy_documents", None)) and callable(
        getattr(connector, "legacy_scope", None)
    )


def should_run(state: MigrationState | None, mode: str) -> bool:
    """A sync starts or continues it; the maintenance task also re-runs an
    unclean finish. A clean finish is never run again."""
    if state is None or state.status == STATUS_RUNNING:
        return True
    return mode == MODE_MAINTENANCE and state.status == STATUS_COMPLETED_WITH_ISSUES


async def migrate_legacy_mongodb_ids(
    *,
    config: Any,
    connector: _LegacyConnector,
    pipeline: Any,
    knowledge_store: Any,
    state_store: MigrationStateStore,
    mode: str = MODE_SYNC,
    page_size: int = DEFAULT_PAGE_SIZE,
    max_documents: int = DEFAULT_MAX_DOCUMENTS,
    check: Any = None,
) -> dict[str, Any]:
    """Run (or continue) the migration for one Source; returns the state as a dict.

    ``check`` is called before every page (the sync passes its lease check, so a
    run that lost the Source's lock stops). Errors from the upstream read, the
    legal-hold check or the store propagate after the error is recorded in the
    persisted state (status stays ``running``, cursor at the failed page).
    """
    from app.tenancy.context import PlanTier, TenantContext

    state = await state_store.get(config.tenant_id, config.source_id)
    if not should_run(state, mode):
        assert state is not None
        return {**state.as_dict(), "ran": False}
    if state is None or state.status != STATUS_RUNNING:
        # First run, or a fresh pass over an unclean finish (maintenance).
        state = MigrationState(tenant_id=config.tenant_id, source_id=config.source_id)
        await state_store.save(state)
    if not config.collection_id:
        raise RuntimeError(f"source {config.source_id} has no collection_id")

    tenant_ctx = TenantContext(
        tenant_id=config.tenant_id, api_key_id="mongodb-id-migration", plan=PlanTier.FREE
    )
    database, collections = connector.legacy_scope(config)
    scanned_this_run = 0
    try:
        exhausted = False
        while scanned_this_run < max_documents:
            if check is not None:
                check()
            docs = await knowledge_store.list_source_documents_async(
                tenant_ctx=tenant_ctx,
                collection_id=config.collection_id,
                source_id=config.source_id,
                limit=page_size,
                after=state.cursor,
            )
            if not docs:
                exhausted = True
                break
            await _migrate_page(
                docs,
                state=state,
                config=config,
                connector=connector,
                pipeline=pipeline,
                knowledge_store=knowledge_store,
                tenant_ctx=tenant_ctx,
                database=database,
                collections=collections,
            )
            state.scanned += len(docs)
            scanned_this_run += len(docs)
            state.cursor = str(docs[-1]["id"])
            state.last_error = None
            exhausted = len(docs) < page_size
            if exhausted:
                break
            await state_store.save(state)  # resumable after every page
        if exhausted:
            _finish(state)
        await state_store.save(state)
    except Exception as exc:
        state.last_error = f"{type(exc).__name__}: {exc}"[:2000]
        await state_store.save(state)
        raise
    _log.info(
        "mongodb_id_migration source=%s tenant=%s status=%s scanned=%d migrated=%d "
        "deleted=%d held=%d skipped=%d failed=%d",
        config.source_id,
        config.tenant_id,
        state.status,
        state.scanned,
        state.migrated,
        state.deleted,
        state.held,
        state.skipped,
        state.failed,
    )
    return {**state.as_dict(), "ran": True}


def _finish(state: MigrationState) -> None:
    clean = not (state.held or state.skipped or state.failed)
    state.status = STATUS_COMPLETED if clean else STATUS_COMPLETED_WITH_ISSUES
    state.completed_at = datetime.now(UTC)


async def _migrate_page(
    docs: list[dict[str, Any]],
    *,
    state: MigrationState,
    config: Any,
    connector: _LegacyConnector,
    pipeline: Any,
    knowledge_store: Any,
    tenant_ctx: Any,
    database: str,
    collections: list[str],
) -> None:
    refs = [
        ref
        for doc in docs
        if (ref := legacy_ref(str(doc.get("id") or ""), str(doc.get("source_url") or "")))
    ]
    if not refs:
        return
    held = await knowledge_store.held_document_ids_async(
        config.collection_id, [r.document_id for r in refs], tenant_ctx=tenant_ctx
    )
    todo: list[LegacyRef] = []
    for ref in refs:
        if ref.document_id in held:
            state.held += 1
        elif ref.database != database or (collections and ref.collection not in collections):
            state.skipped += 1
        else:
            todo.append(ref)
    if not todo:
        return
    upstream = await connector.read_legacy_documents(
        config, [(r.collection, r.key) for r in todo]
    )
    for ref in todo:
        raws = list(upstream.get((ref.collection, ref.key)) or [])
        try:
            done = await _migrate_one(
                ref, raws, config=config, pipeline=pipeline,
                knowledge_store=knowledge_store, tenant_ctx=tenant_ctx,
            )
        except Exception:
            _log.exception(
                "mongodb_id_migration_document_failed source=%s doc=%s",
                config.source_id,
                ref.document_id,
            )
            done = None
        if done == "deleted":
            state.deleted += 1
        elif done == "migrated":
            state.migrated += 1
        else:
            state.failed += 1


async def _migrate_one(
    ref: LegacyRef,
    raws: list[Any],
    *,
    config: Any,
    pipeline: Any,
    knowledge_store: Any,
    tenant_ctx: Any,
) -> str | None:
    """'migrated' / 'deleted' when the legacy copy is gone, None when it was kept."""
    if not raws:
        # Gone upstream (the read itself succeeded): the stale copy goes too.
        await knowledge_store.delete_document_async(
            ref.document_id, collection_id=config.collection_id, tenant_ctx=tenant_ctx
        )
        return "deleted"
    present = await knowledge_store.existing_document_ids_async(
        config.collection_id, [r.doc_id for r in raws], tenant_ctx=tenant_ctx
    )
    superseded = False
    for raw in raws:
        if raw.doc_id in present:
            continue  # already indexed under its v8 id (e.g. read from the change stream)
        result = await pipeline.ingest(
            raw, config, supersedes=None if superseded else ref.document_id
        )
        if result.status == "indexed":
            superseded = True  # written; the legacy copy went in the same transaction
            continue
        if result.status == "skipped" and result.skip_reason == "dedup":
            continue  # the same content is indexed under another document
        _log.warning(
            "mongodb_id_migration_write_failed source=%s legacy=%s new=%s: %s",
            config.source_id,
            ref.document_id,
            raw.doc_id,
            result.error or result.skip_reason,
        )
        return None
    if not superseded:
        await knowledge_store.delete_document_async(
            ref.document_id, collection_id=config.collection_id, tenant_ctx=tenant_ctx
        )
    return "migrated"
