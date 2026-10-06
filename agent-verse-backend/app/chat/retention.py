"""Chat session retention: enforce ``chat_sessions.ttl_days`` (CHAT-SEC-3).

A session with ``ttl_days`` set (1..3650, ``PATCH /chat/sessions/{id}``) expires
``ttl_days`` days after its last activity (``updated_at``: a new or edited
message moves it). ``ttl_days`` NULL never expires, and a pinned session never
expires (the chat spec: "pinned sessions never expire").

:func:`purge_expired_chat_sessions` deletes expired sessions with their
messages and artifacts, fleet-wide, on the BYPASSRLS maintenance role
(``system_session``; under the app role it fails loudly instead of deleting
nothing). It is bounded at every step:

* the distinct ``ttl_days`` values are read with a loose index scan, then each
  value's expired sessions are an index range scan of
  ``ix_chat_sessions_ttl_expiry (ttl_days, updated_at, id)``;
* ``batch_size`` sessions per batch, ``max_batches`` batches per run (the next
  run continues, ``truncated`` says so);
* messages are deleted ``message_batch`` rows per transaction, so a session with
  millions of messages never becomes one huge transaction.

Legal hold: a session is never deleted while an active hold covers its tenant
(resource_type ``tenant``), the session (``resource_ids``) or its owner
(``user_ids``). The hold check is part of every SELECT/DELETE predicate, so a
hold placed mid-run stops the remaining deletes; an unreadable hold table
fails the statement (nothing deleted).

Every predicate re-checks "still expired, unpinned, unheld", so a session that
got a new message (or was pinned) while a run was deleting it is kept.

Knowledge: before an owned session is deleted, ``on_owned_sessions`` is awaited
with ``(tenant_id, owner_user_id, session_ids)`` to remove its transcript from
the index (the beat task queues ``ingestion.chat_transcripts_purge``). When that
raises, the batch's sessions are not deleted and the error propagates.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any

MAX_TTL_DAYS = 3650

OwnedSessionsHook = Callable[[str, str, list[str]], Awaitable[None]]

# A session row ``s`` that is expired, unpinned and under no active legal hold.
_EXPIRED = (
    "s.ttl_days = :d AND s.pinned IS FALSE "
    "AND s.updated_at < now() - make_interval(days => :d) "
    "AND NOT EXISTS (SELECT 1 FROM legal_holds lh WHERE lh.tenant_id = s.tenant_id "
    "AND lh.status = 'active' AND (lh.resource_type = 'tenant' "
    "OR lh.resource_ids @> jsonb_build_array(s.id) "
    "OR (s.owner_user_id IS NOT NULL AND lh.user_ids @> jsonb_build_array(s.owner_user_id))))"
)

# Loose index scan over ix_chat_sessions_ttl_expiry: the distinct ttl values.
_TTL_VALUES = (
    "WITH RECURSIVE v(d) AS ("
    " SELECT min(ttl_days) FROM chat_sessions"
    " WHERE ttl_days > 0 AND ttl_days IS NOT NULL AND pinned IS FALSE"
    " UNION ALL"
    " SELECT (SELECT min(ttl_days) FROM chat_sessions"
    "         WHERE ttl_days > v.d AND pinned IS FALSE)"
    " FROM v WHERE v.d IS NOT NULL"
    ") SELECT d FROM v WHERE d IS NOT NULL"
)


@dataclass
class RetentionReport:
    sessions_deleted: int = 0
    messages_deleted: int = 0
    artifacts_deleted: int = 0
    truncated: bool = False
    transcript_purges: int = 0
    tenants: set[str] = field(default_factory=set)

    def as_dict(self) -> dict[str, Any]:
        return {
            "sessions_deleted": self.sessions_deleted,
            "messages_deleted": self.messages_deleted,
            "artifacts_deleted": self.artifacts_deleted,
            "transcript_purges": self.transcript_purges,
            "tenants": len(self.tenants),
            "truncated": self.truncated,
        }


async def purge_expired_chat_sessions(
    session_factory: Any,
    *,
    on_owned_sessions: OwnedSessionsHook | None = None,
    batch_size: int = 200,
    max_batches: int = 50,
    message_batch: int = 5000,
    max_message_batches: int = 200,
) -> RetentionReport:
    """Delete expired chat sessions (see the module doc). Errors propagate."""
    from sqlalchemy import text

    from app.db.rls import system_session

    report = RetentionReport()

    async def run(sql: str, params: dict[str, Any]) -> Any:
        async with session_factory() as session, session.begin(), system_session(session):
            return await session.execute(text(sql), params)

    ttl_values = [int(r[0]) for r in (await run(_TTL_VALUES, {})).fetchall()]
    batches = 0
    for days in ttl_values:
        if days > MAX_TTL_DAYS:
            continue
        while True:
            if batches >= max_batches:
                report.truncated = True
                return report
            victims = (
                await run(
                    f"SELECT s.id, s.tenant_id, s.owner_user_id FROM chat_sessions s "
                    f"WHERE {_EXPIRED} ORDER BY s.updated_at, s.id LIMIT :n",
                    {"d": days, "n": batch_size},
                )
            ).fetchall()
            if not victims:
                break
            batches += 1  # a batch that found sessions (an empty ttl value is free)
            ids = [str(v[0]) for v in victims]
            # 1. The transcripts leave the index first (fail closed: no delete).
            owned: dict[tuple[str, str], list[str]] = {}
            for sid, tid, owner in victims:
                if owner:
                    owned.setdefault((str(tid), str(owner)), []).append(str(sid))
            if owned and on_owned_sessions is not None:
                for (tid, owner), sids in owned.items():
                    await on_owned_sessions(tid, owner, sids)
                    report.transcript_purges += 1
            # 2. Messages, bounded per transaction, only while still expired.
            for _ in range(max_message_batches):
                gone = int(
                    (
                        await run(
                            "DELETE FROM chat_messages WHERE id IN ("
                            " SELECT m.id FROM chat_messages m JOIN chat_sessions s"
                            " ON s.id = m.session_id AND s.tenant_id = m.tenant_id"
                            f" WHERE m.session_id = ANY(CAST(:ids AS text[])) AND {_EXPIRED}"
                            " LIMIT :m)",
                            {"ids": ids, "d": days, "m": message_batch},
                        )
                    ).rowcount
                    or 0
                )
                report.messages_deleted += gone
                if gone < message_batch:
                    break
            else:
                report.truncated = True  # a huge session: the next run continues
                return report
            # 3. The now-empty sessions (and their artifacts), still expired.
            async with session_factory() as session, session.begin(), system_session(session):
                locked = [
                    str(r[0])
                    for r in (
                        await session.execute(
                            text(
                                "SELECT s.id FROM chat_sessions s "
                                "WHERE s.id = ANY(CAST(:ids AS text[])) "
                                f"AND {_EXPIRED} AND NOT EXISTS (SELECT 1 FROM chat_messages m "
                                "WHERE m.session_id = s.id) FOR UPDATE OF s SKIP LOCKED"
                            ),
                            {"ids": ids, "d": days},
                        )
                    ).fetchall()
                ]
                deleted: list[Any] = []
                if locked:
                    arts = await session.execute(
                        text(
                            "DELETE FROM chat_artifacts "
                            "WHERE session_id = ANY(CAST(:ids AS text[]))"
                        ),
                        {"ids": locked},
                    )
                    report.artifacts_deleted += int(arts.rowcount or 0)
                    await session.execute(
                        text(
                            "DELETE FROM chat_message_usage "
                            "WHERE session_id = ANY(CAST(:ids AS text[]))"
                        ),
                        {"ids": locked},
                    )
                    deleted = (
                        await session.execute(
                            text(
                                "DELETE FROM chat_sessions "
                                "WHERE id = ANY(CAST(:ids AS text[])) RETURNING id, tenant_id"
                            ),
                            {"ids": locked},
                        )
                    ).fetchall()
                done = [str(r[0]) for r in deleted]
            report.sessions_deleted += len(done)
            report.tenants.update(str(r[1]) for r in deleted)
            if len(victims) < batch_size:
                break
    return report
