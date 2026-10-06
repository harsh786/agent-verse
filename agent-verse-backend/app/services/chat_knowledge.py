"""Chat transcripts as knowledge — double consent and purge (owner decision 7).

A person's chats become searchable knowledge only when BOTH hold, each off by
default:

* the tenant admin switched the ``chat_transcript`` kind on
  (``tenants.chat_transcripts_kb_enabled``, admin-only
  ``PUT /tenants/me/chat-transcripts-knowledge``), and
* that person opted in for their own chats (``chat_kb_consents``,
  ``PUT /chat/settings/knowledge``; revocable).

Only sessions the person created (``chat_sessions.owner_user_id``) are ever
indexed, under that person's id, by an ``agent_generated`` Source listening for
``chat_transcript`` (see ``app.ingestion.connectors.agent_generated_connector``).

Revoking the opt-in, turning the tenant switch off, deleting a chat session or a
data-subject erasure remove the transcripts already indexed with
:func:`purge_transcripts`: bounded keyset pages over the chunk tables (the
transcript chunks carry ``metadata.origin`` ``{kind: chat_transcript, user_id,
chat_session_id}``), one legal-hold query per page — a held document is kept and
reported, never deleted — and at most ``batch * max_batches`` documents per call
(``truncated`` tells the caller to continue).

Every read and write carries an explicit ``tenant_id`` predicate under the
tenant's RLS context; an unreadable switch or consent raises
:class:`ChatKnowledgeUnavailableError` (never a silent "off" that would hide a
failure, never a silent "on").
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

KIND_CHAT_TRANSCRIPT = "chat_transcript"
PURGE_BATCH = 200
PURGE_MAX_BATCHES = 25
_CHUNK_TABLES = tuple(f"knowledge_chunks_{dim}" for dim in (768, 1024, 1536, 2048, 3072))


class ChatKnowledgeUnavailableError(RuntimeError):
    """The switch, a consent or the index could not be read or written."""


@dataclass(frozen=True)
class ConsentState:
    opted_in: bool
    opted_in_at: datetime | None = None
    revoked_at: datetime | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "opted_in": self.opted_in,
            "opted_in_at": self.opted_in_at.isoformat() if self.opted_in_at else None,
            "revoked_at": self.revoked_at.isoformat() if self.revoked_at else None,
        }


@dataclass
class PurgeReport:
    removed: int = 0
    held: int = 0
    truncated: bool = False
    held_document_ids: list[str] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return {
            "removed_documents": self.removed,
            "held_documents": self.held,
            "held_document_ids": self.held_document_ids[:100],
            "pending": self.truncated,
        }


def _now() -> datetime:
    return datetime.now(UTC)


class ChatKnowledgeSettings:
    """The tenant switch and the per-user consents; Postgres, or in memory."""

    def __init__(self, *, db: Any = None, memory: dict[str, Any] | None = None) -> None:
        self._db = db
        self._mem: dict[str, Any] = memory if memory is not None else {}
        self._mem.setdefault("tenants", {})
        self._mem.setdefault("consents", {})

    async def _run(self, tenant_id: str, sql: str, params: dict[str, Any]) -> Any:
        from sqlalchemy import text

        from app.db.rls import sqlalchemy_rls_context

        try:
            async with (
                self._db() as session,
                session.begin(),
                sqlalchemy_rls_context(session, tenant_id),
            ):
                return await session.execute(text(sql), params)
        except Exception as exc:
            raise ChatKnowledgeUnavailableError(
                f"chat knowledge settings unavailable: {type(exc).__name__}"
            ) from exc

    # ── tenant switch ────────────────────────────────────────────────────────

    async def tenant_enabled(self, tenant_id: str) -> bool:
        if self._db is None:
            return bool(self._mem["tenants"].get(tenant_id, False))
        result = await self._run(
            tenant_id,
            "SELECT chat_transcripts_kb_enabled FROM tenants WHERE id = :tid",
            {"tid": tenant_id},
        )
        return bool(result.scalar_one_or_none())

    async def set_tenant_enabled(self, tenant_id: str, enabled: bool) -> None:
        if self._db is None:
            self._mem["tenants"][tenant_id] = enabled
            return
        result = await self._run(
            tenant_id,
            "UPDATE tenants SET chat_transcripts_kb_enabled = :v, updated_at = now() "
            "WHERE id = :tid",
            {"v": enabled, "tid": tenant_id},
        )
        if result.rowcount != 1:
            raise ChatKnowledgeUnavailableError("tenant not found")

    # ── per-user opt-in ──────────────────────────────────────────────────────

    async def consent(self, tenant_id: str, user_id: str) -> ConsentState:
        if self._db is None:
            found = self._mem["consents"].get((tenant_id, user_id))
            return found if isinstance(found, ConsentState) else ConsentState(False)
        result = await self._run(
            tenant_id,
            "SELECT opted_in, opted_in_at, revoked_at FROM chat_kb_consents "
            "WHERE tenant_id = :tid AND user_id = :uid",
            {"tid": tenant_id, "uid": user_id},
        )
        row = result.fetchone()
        if row is None:
            return ConsentState(False)
        return ConsentState(bool(row[0]), row[1], row[2])

    async def set_consent(self, tenant_id: str, user_id: str, opted_in: bool) -> ConsentState:
        if not user_id:
            raise ValueError("a chat knowledge opt-in belongs to a person (user id required)")
        if self._db is None:
            prev = await self.consent(tenant_id, user_id)
            now = _now()
            state = (
                ConsentState(True, now, prev.revoked_at)
                if opted_in
                else ConsentState(False, prev.opted_in_at, now)
            )
            self._mem["consents"][(tenant_id, user_id)] = state
            return state
        stamp = "opted_in_at" if opted_in else "revoked_at"
        result = await self._run(
            tenant_id,
            "INSERT INTO chat_kb_consents (tenant_id, user_id, opted_in, "
            f"{stamp}, updated_at) VALUES (:tid, :uid, :v, now(), now()) "
            "ON CONFLICT (tenant_id, user_id) DO UPDATE SET opted_in = EXCLUDED.opted_in, "
            f"{stamp} = now(), updated_at = now() "
            "RETURNING opted_in, opted_in_at, revoked_at",
            {"tid": tenant_id, "uid": user_id, "v": opted_in},
        )
        row = result.fetchone()
        return ConsentState(bool(row[0]), row[1], row[2])


def chat_knowledge_for(state: Any) -> ChatKnowledgeSettings:
    """The settings over this app's DB (or a per-app in-memory map without one)."""
    memory = getattr(state, "chat_knowledge_settings", None)
    if memory is None:
        memory = {}
        state.chat_knowledge_settings = memory
    return ChatKnowledgeSettings(db=getattr(state, "db_session_factory", None), memory=memory)


# ── purge ────────────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class _Filter:
    user_id: str | None
    session_ids: tuple[str, ...] | None
    collection_id: str | None
    unconsented_only: bool


def _origin_matches(origin: Any, flt: _Filter) -> bool:
    if not isinstance(origin, dict) or origin.get("kind") != KIND_CHAT_TRANSCRIPT:
        return False
    if flt.user_id is not None and origin.get("user_id") != flt.user_id:
        return False
    return flt.session_ids is None or origin.get("chat_session_id") in flt.session_ids


async def _memory_page(
    store: Any,
    tenant_id: str,
    flt: _Filter,
    settings: ChatKnowledgeSettings | None,
    after: tuple[str, str],
    limit: int,
) -> list[tuple[str, str]]:
    found: set[tuple[str, str]] = set()
    for (tid, cid), coll in list(getattr(store, "_data", {}).items()):
        if tid != tenant_id or (flt.collection_id and cid != flt.collection_id):
            continue
        for chunk in coll.chunks:
            origin = (chunk.metadata or {}).get("origin")
            if not _origin_matches(origin, flt):
                continue
            if flt.unconsented_only and settings is not None:
                uid = str(origin.get("user_id") or "")
                if (
                    await settings.tenant_enabled(tenant_id)
                    and uid
                    and (await settings.consent(tenant_id, uid)).opted_in
                ):
                    continue
            found.add((cid, chunk.document_id))
    return sorted(k for k in found if k > after)[:limit]


async def _db_tables(db: Any, tenant_id: str) -> list[str]:
    from sqlalchemy import text

    from app.db.rls import sqlalchemy_rls_context

    async with db() as session, session.begin(), sqlalchemy_rls_context(session, tenant_id):
        rows = (
            await session.execute(
                text(
                    "SELECT t FROM unnest(CAST(:tables AS text[])) AS t "
                    "WHERE to_regclass(t) IS NOT NULL"
                ),
                {"tables": list(_CHUNK_TABLES)},
            )
        ).fetchall()
    return [str(r[0]) for r in rows]


async def _db_page(
    db: Any, table: str, tenant_id: str, flt: _Filter, after: tuple[str, str], limit: int
) -> list[tuple[str, str]]:
    from sqlalchemy import text

    from app.db.rls import sqlalchemy_rls_context

    params: dict[str, Any] = {
        "tid": tenant_id, "kind": KIND_CHAT_TRANSCRIPT, "ac": after[0], "ad": after[1],
        "lim": limit,
    }
    where = ""
    if flt.user_id is not None:
        where += " AND k.metadata->'origin'->>'user_id' = :uid"
        params["uid"] = flt.user_id
    if flt.session_ids is not None:
        where += " AND k.metadata->'origin'->>'chat_session_id' = ANY(CAST(:sids AS text[]))"
        params["sids"] = list(flt.session_ids)
    if flt.collection_id:
        where += " AND k.collection_id = :cid"
        params["cid"] = flt.collection_id
    if flt.unconsented_only:
        where += (
            " AND NOT (EXISTS (SELECT 1 FROM tenants t WHERE t.id = k.tenant_id "
            "AND t.chat_transcripts_kb_enabled IS TRUE) AND EXISTS (SELECT 1 FROM "
            "chat_kb_consents c WHERE c.tenant_id = k.tenant_id "
            "AND c.user_id = k.metadata->'origin'->>'user_id' AND c.opted_in IS TRUE))"
        )
    sql = (
        f"SELECT DISTINCT k.collection_id, k.document_id FROM {table} k "
        "WHERE k.tenant_id = :tid AND k.metadata->'origin'->>'kind' = :kind"
        + where
        + " AND (k.collection_id, k.document_id) > (:ac, :ad) "
        "ORDER BY k.collection_id, k.document_id LIMIT :lim"
    )
    async with db() as session, session.begin(), sqlalchemy_rls_context(session, tenant_id):
        rows = (await session.execute(text(sql), params)).fetchall()
    return [(str(r[0]), str(r[1])) for r in rows]


async def purge_transcripts(
    store: Any,
    tenant_id: str,
    *,
    user_id: str | None = None,
    session_ids: list[str] | None = None,
    collection_id: str | None = None,
    unconsented_only: bool = False,
    settings: ChatKnowledgeSettings | None = None,
    batch: int = PURGE_BATCH,
    max_batches: int = PURGE_MAX_BATCHES,
) -> PurgeReport:
    """Delete indexed chat transcripts of the tenant matching the filter.

    ``user_id`` — one person's; ``session_ids`` — those sessions'; neither — every
    transcript of the tenant. ``unconsented_only`` keeps those whose owner still
    consents while the tenant switch is on (the sweep after a sync).

    Held documents are kept and reported. Raises
    :class:`ChatKnowledgeUnavailableError` when a page or its hold check cannot
    be read: nothing on that page is deleted (fail closed).
    """
    from app.tenancy.context import PlanTier, TenantContext

    report = PurgeReport()
    if session_ids is not None and not session_ids:
        return report
    flt = _Filter(
        user_id=user_id,
        session_ids=tuple(session_ids) if session_ids is not None else None,
        collection_id=collection_id,
        unconsented_only=unconsented_only,
    )
    ctx = TenantContext(tenant_id=tenant_id, plan=PlanTier.FREE, api_key_id="chat-knowledge")
    db = getattr(store, "_db", None)
    try:
        tables: list[str | None] = list(await _db_tables(db, tenant_id)) if db else [None]
    except Exception as exc:
        raise ChatKnowledgeUnavailableError(f"knowledge index unavailable: {exc}") from exc
    pages = 0
    for table in tables:
        after = ("", "")
        while True:
            if pages >= max_batches:
                report.truncated = True
                return report
            pages += 1
            try:
                page = (
                    await _db_page(db, table, tenant_id, flt, after, batch)
                    if db is not None and table is not None
                    else await _memory_page(store, tenant_id, flt, settings, after, batch)
                )
                by_collection: dict[str, list[str]] = {}
                for cid, did in page:
                    by_collection.setdefault(cid, []).append(did)
                held: dict[str, set[str]] = {
                    cid: set(await store.held_document_ids_async(cid, ids, tenant_ctx=ctx))
                    for cid, ids in by_collection.items()
                }
            except Exception as exc:
                raise ChatKnowledgeUnavailableError(
                    f"chat transcripts could not be checked for legal hold: "
                    f"{type(exc).__name__}"
                ) from exc
            for cid, did in page:
                if did in held.get(cid, set()):
                    report.held += 1
                    report.held_document_ids.append(did)
                    continue
                if await store.delete_document_async(did, collection_id=cid, tenant_ctx=ctx):
                    report.removed += 1
            if not page:
                break
            after = page[-1]
            if len(page) < batch:
                break
    return report


PURGE_TASK = "ingestion.chat_transcripts_purge"


def enqueue_purge_continuation(
    tenant_id: str,
    *,
    user_id: str | None = None,
    session_ids: list[str] | None = None,
    unconsented_only: bool = False,
) -> None:
    """Queue the rest of a truncated purge (raises when the broker refuses)."""
    from app.scaling.celery_app import celery_app

    celery_app.send_task(
        PURGE_TASK,
        kwargs={
            "tenant_id": tenant_id,
            "user_id": user_id,
            "session_ids": session_ids,
            "unconsented_only": unconsented_only,
        },
        queue="ingestion",
        countdown=1,
    )


async def remove_transcripts(
    state: Any,
    tenant_id: str,
    *,
    user_id: str | None = None,
    session_ids: list[str] | None = None,
) -> dict[str, Any]:
    """Purge through the app's knowledge store; a truncated purge continues in a task.

    Without ``session_ids`` (a revocation, the tenant switch turned off) only what
    is no longer consented at the time each page is read is removed, so a person
    who opts in again meanwhile, or a switch turned back on, is not undone by a
    late continuation. Session ids (deleted sessions) are removed outright.
    Raises :class:`ChatKnowledgeUnavailableError` when the purge could not run.
    """
    import asyncio
    import logging

    store = getattr(state, "knowledge_store", None)
    if store is None:
        return PurgeReport().as_dict()
    unconsented_only = session_ids is None
    report = await purge_transcripts(
        store, tenant_id, user_id=user_id, session_ids=session_ids,
        unconsented_only=unconsented_only, settings=chat_knowledge_for(state),
    )
    out = report.as_dict()
    if report.truncated:
        try:
            await asyncio.to_thread(
                enqueue_purge_continuation, tenant_id, user_id=user_id,
                session_ids=session_ids, unconsented_only=unconsented_only,
            )
            out["continuation"] = "queued"
        except Exception as exc:
            logging.getLogger(__name__).warning(
                "chat_transcripts_purge_continuation_failed tenant=%s: %s", tenant_id, exc
            )
            out["continuation"] = "not queued: repeat the request to remove the rest"
    return out
