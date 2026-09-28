"""SourceConfigStore — durable persistence for ingestion Sources (item 6).

Backs the ``source_configs`` table (migration 0124) so a Source created via the
API survives restarts and is visible to the Celery worker/scheduler process
(cross-process), which is what makes scheduled/recurring source sync possible.
Falls back to an in-memory dict when no DB session factory is wired (tests / dev
without Postgres), mirroring the other stores in this codebase.

Two session factories, never interchangeable:

* ``db`` — the application's own (least-privilege, NOBYPASSRLS) factory. Every
  per-tenant call — the ``/sources*`` API, the manual and scheduled sync of one
  Source — runs inside a transaction with the ``app.tenant_id`` GUC set
  (``sqlalchemy_rls_context``) *and* an explicit ``tenant_id`` predicate. The
  Celery worker syncing one Source knows its tenant, so it goes through here too.
* ``system_db`` — the maintenance-role (BYPASSRLS) factory, used ONLY by
  ``list_due``: the Celery-beat scan for due Sources, which is genuinely
  cross-tenant. ``system_session`` is useless on the application factory — under
  the NOBYPASSRLS role every statement after it fails with "query would be
  affected by row-level security" — which is exactly how the due-scan (and the
  since-removed ``get_system``) broke once the API ran least-privilege.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from app.ingestion.source_config import SourceConfig, SourceFamily
from app.observability.logging import get_logger

# Bounded batch for the due-source beat scan, most-overdue first. An unbounded
# scan is fine in steady state but returns EVERY source at once after an outage,
# when they are all simultaneously due — the one moment the scheduler must not
# also be the thing that falls over. The remainder is picked up next tick.
_DUE_SCAN_LIMIT = 500

_log = get_logger(__name__)

# Every persisted SourceConfig field, in one place (column == attribute name).
_JSON_FIELDS = (
    "connection_config",
    "include_patterns",
    "exclude_patterns",
    "allowed_roles",
    "allowed_user_ids",
    "tags",
)
_SCALAR_FIELDS = (
    "name",
    "family",
    "source_type",
    "enabled",
    "sync_mode",
    "sync_interval_seconds",
    "cursor_field",
    "cursor_value",
    "max_doc_size_bytes",
    "chunking_strategy",
    "chunk_size_tokens",
    "chunk_overlap_tokens",
    "embedding_model",
    "language_hint",
    "inherit_source_acl",
    "min_quality_score",
    "pii_action",
    "near_dup_threshold",
    "freshness_ttl_seconds",
    "collection_id",
    "total_docs_indexed",
    "total_chunks",
    "consecutive_failures",
    "version",
)


def _iso(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, datetime):
        return value.isoformat()
    return str(value)


def _row_to_config(row: Any) -> SourceConfig:
    """Map a source_configs row (SQLAlchemy mapping) to a SourceConfig."""
    d = dict(row)
    family_raw = d.get("family") or SourceFamily.WEB.value
    try:
        family = SourceFamily(family_raw)
    except ValueError:
        family = SourceFamily.AGENT_GENERATED
    # The table's primary key column is ``id`` (0109/0110), which is the
    # SourceConfig.source_id.
    kwargs: dict[str, Any] = {
        "source_id": d.get("source_id") or d["id"],
        "tenant_id": d["tenant_id"],
        "family": family,
    }
    for f in _SCALAR_FIELDS:
        if f == "family":
            continue
        if f in d and d[f] is not None:
            kwargs[f] = d[f]
    for f in _JSON_FIELDS:
        val = d.get(f)
        if val is not None:
            kwargs[f] = val
    kwargs["last_synced_at"] = _iso(d.get("last_synced_at")) or None
    kwargs["created_at"] = _iso(d.get("created_at"))
    kwargs["updated_at"] = _iso(d.get("updated_at"))
    return SourceConfig(**kwargs)


class SourceConfigStore:
    def __init__(self, db: Any = None, *, system_db: Any = None) -> None:
        """``db`` serves every tenant-scoped call (RLS); ``system_db`` serves only
        the cross-tenant ``list_due`` beat scan. With neither, the store is an
        in-memory dict (tests / dev without Postgres)."""
        self._db = db
        self._system_db = system_db
        self._mem: dict[str, SourceConfig] = {}

    # ── writes ────────────────────────────────────────────────────────────────

    async def create(self, config: SourceConfig) -> SourceConfig:
        if self._db is None:
            self._mem[config.source_id] = config
            return config
        from sqlalchemy import text

        from app.db.rls import sqlalchemy_rls_context

        fam = config.family.value if hasattr(config.family, "value") else str(config.family)
        params = {
            "id": config.source_id,
            "tenant_id": config.tenant_id,
            "family": fam,
        }
        for f in _SCALAR_FIELDS:
            if f != "family":
                params[f] = getattr(config, f)
        import json as _json

        for f in _JSON_FIELDS:
            params[f] = _json.dumps(getattr(config, f))
        cols = ["id", "tenant_id", "family", *[f for f in _SCALAR_FIELDS if f != "family"]]
        json_cols = list(_JSON_FIELDS)
        placeholders = [f":{c}" for c in cols] + [f"CAST(:{c} AS jsonb)" for c in json_cols]
        all_cols = cols + json_cols
        async with (
            self._db() as session,
            session.begin(),
            sqlalchemy_rls_context(session, config.tenant_id),
        ):
            await session.execute(
                text(
                    f"INSERT INTO source_configs ({', '.join(all_cols)}) "
                    f"VALUES ({', '.join(placeholders)})"
                ),
                params,
            )
        _log.info("source.created", source_id=config.source_id, source_type=config.source_type)
        return config

    async def update(
        self, source_id: str, tenant_id: str, **fields: Any
    ) -> SourceConfig | None:
        if self._db is None:
            cfg = self._mem.get(source_id)
            if cfg is None or cfg.tenant_id != tenant_id:
                return None
            for k, v in fields.items():
                if hasattr(cfg, k):
                    setattr(cfg, k, v)
            return cfg
        if not fields:
            return await self.get(source_id, tenant_id)
        import json as _json

        from sqlalchemy import text

        from app.db.rls import sqlalchemy_rls_context

        set_parts: list[str] = []
        params: dict[str, Any] = {"source_id": source_id, "tenant_id": tenant_id}
        for k, v in fields.items():
            if k in _JSON_FIELDS:
                set_parts.append(f"{k} = CAST(:{k} AS jsonb)")
                params[k] = _json.dumps(v)
            elif k == "family":
                set_parts.append("family = :family")
                params["family"] = v.value if hasattr(v, "value") else str(v)
            elif k in _SCALAR_FIELDS or k in ("last_synced_at",):
                set_parts.append(f"{k} = :{k}")
                params[k] = v
        set_parts.append("updated_at = now()")
        async with (
            self._db() as session,
            session.begin(),
            sqlalchemy_rls_context(session, tenant_id),
        ):
            await session.execute(
                text(
                    f"UPDATE source_configs SET {', '.join(set_parts)} "
                    "WHERE id = :source_id AND tenant_id = :tenant_id"
                ),
                params,
            )
        return await self.get(source_id, tenant_id)

    async def delete(self, source_id: str, tenant_id: str) -> bool:
        if self._db is None:
            cfg = self._mem.get(source_id)
            if cfg is None or cfg.tenant_id != tenant_id:
                return False
            del self._mem[source_id]
            return True
        from sqlalchemy import text

        from app.db.rls import sqlalchemy_rls_context

        async with (
            self._db() as session,
            session.begin(),
            sqlalchemy_rls_context(session, tenant_id),
        ):
            res = await session.execute(
                text(
                    "DELETE FROM source_configs "
                    "WHERE id = :id AND tenant_id = :tid"
                ),
                {"id": source_id, "tid": tenant_id},
            )
        return bool(res.rowcount)

    async def mark_synced(
        self, source_id: str, tenant_id: str, *, docs_indexed: int, chunks: int, failed: int
    ) -> None:
        """Advance sync stats after a run — also sets last_synced_at so the beat
        due-scan reschedules the next sync one interval out."""
        if self._db is None:
            cfg = self._mem.get(source_id)
            if cfg is not None:
                cfg.last_synced_at = datetime.now(UTC).isoformat()
                cfg.total_docs_indexed += docs_indexed
                cfg.total_chunks += chunks
                cfg.consecutive_failures = 0 if failed == 0 else cfg.consecutive_failures + 1
            return
        from sqlalchemy import text

        from app.db.rls import sqlalchemy_rls_context

        async with (
            self._db() as session,
            session.begin(),
            sqlalchemy_rls_context(session, tenant_id),
        ):
            await session.execute(
                text(
                    "UPDATE source_configs SET "
                    "last_synced_at = now(), "
                    "total_docs_indexed = total_docs_indexed + :d, "
                    "total_chunks = total_chunks + :c, "
                    "consecutive_failures = CASE WHEN :f = 0 THEN 0 "
                    "ELSE consecutive_failures + 1 END, "
                    "updated_at = now() "
                    "WHERE id = :id AND tenant_id = :tid"
                ),
                {"d": docs_indexed, "c": chunks, "f": failed, "id": source_id, "tid": tenant_id},
            )

    # ── reads ─────────────────────────────────────────────────────────────────

    async def get(self, source_id: str, tenant_id: str) -> SourceConfig | None:
        if self._db is None:
            cfg = self._mem.get(source_id)
            return cfg if (cfg and cfg.tenant_id == tenant_id) else None
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
                        "SELECT * FROM source_configs "
                        "WHERE id = :id AND tenant_id = :tid"
                    ),
                    {"id": source_id, "tid": tenant_id},
                )
            ).mappings().first()
        return _row_to_config(row) if row else None

    async def list(self, tenant_id: str) -> list[SourceConfig]:
        if self._db is None:
            return [c for c in self._mem.values() if c.tenant_id == tenant_id]
        from sqlalchemy import text

        from app.db.rls import sqlalchemy_rls_context

        async with (
            self._db() as session,
            session.begin(),
            sqlalchemy_rls_context(session, tenant_id),
        ):
            rows = (
                await session.execute(
                    text("SELECT * FROM source_configs WHERE tenant_id = :tid ORDER BY created_at"),
                    {"tid": tenant_id},
                )
            ).mappings().all()
        return [_row_to_config(r) for r in rows]

    async def list_due(self) -> list[tuple[str, str]]:
        """System-wide scan of enabled, non-streaming sources whose next sync is
        due (never synced, or last_synced_at + interval <= now). Returns
        (source_id, tenant_id) pairs for the beat dispatcher.

        This is the one genuinely cross-tenant read in the store, so it runs on
        the maintenance-role factory (``system_db``, falling back to
        ``get_system_session_factory()``), never on the request factory. It is
        called only by Celery beat — never on a request path.
        """
        if self._db is None and self._system_db is None:
            now = datetime.now(UTC)
            due: list[tuple[str, str]] = []
            for c in self._mem.values():
                if not c.enabled or c.sync_mode == "streaming":
                    continue
                if not c.last_synced_at:
                    due.append((c.source_id, c.tenant_id))
                    continue
                try:
                    last = datetime.fromisoformat(c.last_synced_at.replace("Z", "+00:00"))
                    if (now - last).total_seconds() >= c.sync_interval_seconds:
                        due.append((c.source_id, c.tenant_id))
                except ValueError:
                    due.append((c.source_id, c.tenant_id))
            return due
        from sqlalchemy import text

        from app.db.rls import system_session

        system_db = self._system_db
        if system_db is None:
            from app.db.session import get_system_session_factory

            system_db = get_system_session_factory()

        async with system_db() as session, session.begin(), system_session(session):
            rows = await session.execute(
                text(
                    "SELECT id AS source_id, tenant_id FROM source_configs "
                    "WHERE enabled IS TRUE AND sync_mode <> 'streaming' AND ("
                    "  last_synced_at IS NULL OR "
                    "  last_synced_at + (sync_interval_seconds || ' seconds')::interval <= now()"
                    ") "
                    # Bounded batch, most-overdue first — see _DUE_SCAN_LIMIT.
                    "ORDER BY last_synced_at ASC NULLS FIRST LIMIT :lim"
                ),
                {"lim": _DUE_SCAN_LIMIT},
            )
            return [(r.source_id, r.tenant_id) for r in rows]
