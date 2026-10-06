"""PostgresChatRepository — durable chat session + message persistence.

Backs the chat subsystem with the ``chat_sessions`` / ``chat_messages`` tables
(migration 0130) so conversations survive restarts, span workers, and support
long-delayed recall. Every query runs inside an RLS tenant context AND filters
by ``tenant_id`` explicitly (defence-in-depth: a superuser/bypass-RLS role would
otherwise see other tenants' rows).
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.db.rls import sqlalchemy_rls_context

# Bounded defaults so list queries never walk an unbounded history (must scale past
# millions of rows). The message default covers the largest in-process caller
# (conversation-context building requests up to 1000), so slicing callers see no
# regression while the SQL is now LIMIT-bounded and keyset-pageable.
_DEFAULT_MESSAGE_LIMIT = 1000
_DEFAULT_SESSION_LIMIT = 500


def _decode_cursor(cursor: str | None) -> tuple[datetime, str] | None:
    """Split an opaque ``"<timestamp_iso>|<id>"`` keyset cursor into its parts.

    The timestamp is parsed to a timezone-aware ``datetime`` (asyncpg binds a
    ``timestamptz`` parameter from a datetime, not a str). Returns None for a
    missing/malformed cursor so the caller falls back to the first (newest) page
    rather than raising on untrusted input.
    """
    if not cursor or "|" not in cursor:
        return None
    ts, _, row_id = cursor.partition("|")
    if not ts or not row_id:
        return None
    try:
        parsed = datetime.fromisoformat(ts)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed, row_id


# Columns callers may update on a session (allowlist — never interpolate arbitrary keys).
_SESSION_UPDATABLE = frozenset(
    {
        "title",
        "pinned",
        "ttl_days",
        "system_prompt",
        "agent_id",
        "folder_id",
        "show_reasoning",
        "proactive_suggestions",
        "preferred_model",
    }
)


class PostgresChatRepository:
    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._sf = session_factory

    async def create_session(
        self,
        *,
        session_id: str,
        tenant_id: str,
        title: str = "New Chat",
        system_prompt: str | None = None,
        agent_id: str | None = None,
        folder_id: str | None = None,
        owner_user_id: str | None = None,
    ) -> None:
        async with self._sf() as s, s.begin(), sqlalchemy_rls_context(s, tenant_id):
            await s.execute(
                text(
                    "INSERT INTO chat_sessions "
                    "(id, tenant_id, title, system_prompt, agent_id, folder_id, owner_user_id) "
                    "VALUES (:id, :t, :title, :sp, :aid, :fid, :owner)"
                ),
                {
                    "id": session_id,
                    "t": tenant_id,
                    "title": title,
                    "sp": system_prompt,
                    "aid": agent_id,
                    "fid": folder_id,
                    "owner": owner_user_id,
                },
            )

    async def get_session(self, session_id: str, tenant_id: str) -> dict[str, Any] | None:
        async with self._sf() as s, s.begin(), sqlalchemy_rls_context(s, tenant_id):
            row = (
                await s.execute(
                    text("SELECT * FROM chat_sessions WHERE id = :id AND tenant_id = :t"),
                    {"id": session_id, "t": tenant_id},
                )
            ).mappings().one_or_none()
            return dict(row) if row is not None else None

    async def list_sessions(
        self,
        tenant_id: str,
        *,
        limit: int = _DEFAULT_SESSION_LIMIT,
        before: str | None = None,
    ) -> list[dict[str, Any]]:
        """Return up to ``limit`` sessions, newest first.

        Keyset pagination: pass ``before`` — an opaque ``"<updated_at_iso>|<id>"``
        cursor (the (updated_at, id) of the last row already seen) — to page into
        older sessions. Bounded by ``limit`` so a tenant with millions of sessions
        never triggers a full-table walk. Keeps the index-friendly
        ``ORDER BY updated_at DESC`` (idx_chat_sessions_tenant_updated).
        """
        cursor = _decode_cursor(before)
        clause = ""
        params: dict[str, Any] = {"t": tenant_id, "limit": limit}
        if cursor is not None:
            clause = " AND (updated_at, id) < (CAST(:before_ts AS timestamptz), :before_id)"
            params["before_ts"], params["before_id"] = cursor
        async with self._sf() as s, s.begin(), sqlalchemy_rls_context(s, tenant_id):
            rows = (
                await s.execute(
                    text(
                        "SELECT * FROM chat_sessions WHERE tenant_id = :t"
                        f"{clause} "
                        "ORDER BY updated_at DESC, id DESC LIMIT :limit"
                    ),
                    params,
                )
            ).mappings().all()
            return [dict(r) for r in rows]

    async def update_session(self, session_id: str, tenant_id: str, **fields: Any) -> bool:
        updates = {k: v for k, v in fields.items() if k in _SESSION_UPDATABLE}
        if not updates:
            return False
        set_clause = ", ".join(f"{col} = :{col}" for col in updates)
        async with self._sf() as s, s.begin(), sqlalchemy_rls_context(s, tenant_id):
            result = await s.execute(
                text(
                    f"UPDATE chat_sessions SET {set_clause}, updated_at = now() "
                    "WHERE id = :id AND tenant_id = :t"
                ),
                {**updates, "id": session_id, "t": tenant_id},
            )
            return (result.rowcount or 0) > 0

    async def delete_session(self, session_id: str, tenant_id: str) -> bool:
        async with self._sf() as s, s.begin(), sqlalchemy_rls_context(s, tenant_id):
            await s.execute(
                text("DELETE FROM chat_messages WHERE session_id = :id AND tenant_id = :t"),
                {"id": session_id, "t": tenant_id},
            )
            result = await s.execute(
                text("DELETE FROM chat_sessions WHERE id = :id AND tenant_id = :t"),
                {"id": session_id, "t": tenant_id},
            )
            return (result.rowcount or 0) > 0

    # ── Channel-user / principal -> session mappings (migration e5c1a9d3b7f2) ──

    async def resolve_channel_session(
        self,
        *,
        tenant_id: str,
        channel: str,
        channel_user_id: str,
        new_session_id: str,
        title: str,
    ) -> str:
        """Return the durable session id for a channel user, creating it on first contact.

        One transaction: look up the mapping; if absent, insert a new
        ``chat_sessions`` row and claim the mapping with ``INSERT ... ON CONFLICT
        DO NOTHING``. A replica that loses the race (the unique key blocks until
        the winner commits, then does nothing) deletes its own just-inserted
        session and re-selects the winner's — so racing replicas converge on ONE
        session and leave no orphan. DB errors propagate (fail closed).
        """
        key = {"t": tenant_id, "c": channel, "u": channel_user_id}
        select_sql = text(
            "SELECT chat_session_id FROM chat_channel_sessions "
            "WHERE tenant_id = :t AND channel = :c AND channel_user_id = :u"
        )
        async with self._sf() as s, s.begin(), sqlalchemy_rls_context(s, tenant_id):
            found = (await s.execute(select_sql, key)).fetchone()
            if found is not None:
                return str(found[0])
            await s.execute(
                text(
                    "INSERT INTO chat_sessions (id, tenant_id, title) "
                    "VALUES (:id, :t, :title)"
                ),
                {"id": new_session_id, "t": tenant_id, "title": title},
            )
            claimed = (
                await s.execute(
                    text(
                        "INSERT INTO chat_channel_sessions "
                        "(tenant_id, channel, channel_user_id, chat_session_id) "
                        "VALUES (:t, :c, :u, :sid) "
                        "ON CONFLICT (tenant_id, channel, channel_user_id) DO NOTHING "
                        "RETURNING chat_session_id"
                    ),
                    {**key, "sid": new_session_id},
                )
            ).fetchone()
            if claimed is not None:
                return str(claimed[0])
            # Lost the race: drop our session, adopt the winner's.
            await s.execute(
                text("DELETE FROM chat_sessions WHERE id = :id AND tenant_id = :t"),
                {"id": new_session_id, "t": tenant_id},
            )
            winner = (await s.execute(select_sql, key)).fetchone()
            if winner is None:
                raise RuntimeError(
                    "chat_channel_sessions conflict reported but no mapping row is visible"
                )
            return str(winner[0])

    async def get_principal_session(self, tenant_id: str, principal_id: str) -> str | None:
        async with self._sf() as s, s.begin(), sqlalchemy_rls_context(s, tenant_id):
            row = (
                await s.execute(
                    text(
                        "SELECT chat_session_id FROM chat_principal_sessions "
                        "WHERE tenant_id = :t AND principal_id = :p"
                    ),
                    {"t": tenant_id, "p": principal_id},
                )
            ).fetchone()
            return None if row is None else str(row[0])

    async def claim_principal_session(
        self, *, tenant_id: str, principal_id: str, session_id: str
    ) -> str:
        """Bind a principal to a thread if it has none; return the bound session id."""
        params = {"t": tenant_id, "p": principal_id, "sid": session_id}
        async with self._sf() as s, s.begin(), sqlalchemy_rls_context(s, tenant_id):
            await s.execute(
                text(
                    "INSERT INTO chat_principal_sessions "
                    "(tenant_id, principal_id, chat_session_id) VALUES (:t, :p, :sid) "
                    "ON CONFLICT (tenant_id, principal_id) DO NOTHING"
                ),
                params,
            )
            row = (
                await s.execute(
                    text(
                        "SELECT chat_session_id FROM chat_principal_sessions "
                        "WHERE tenant_id = :t AND principal_id = :p"
                    ),
                    params,
                )
            ).fetchone()
            if row is None:
                raise RuntimeError("chat_principal_sessions row missing after upsert")
            return str(row[0])

    async def save_message(
        self,
        *,
        message_id: str,
        session_id: str,
        tenant_id: str,
        role: str,
        content: str,
        intent: str | None = None,
        goal_id: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        import json

        async with self._sf() as s, s.begin(), sqlalchemy_rls_context(s, tenant_id):
            await s.execute(
                text(
                    "INSERT INTO chat_messages "
                    "(id, session_id, tenant_id, role, content, intent, goal_id, metadata) "
                    "VALUES (:id, :sid, :t, :role, :content, :intent, :gid, "
                    "CAST(:meta AS jsonb))"
                ),
                {
                    "id": message_id,
                    "sid": session_id,
                    "t": tenant_id,
                    "role": role,
                    "content": content,
                    "intent": intent,
                    "gid": goal_id,
                    "meta": json.dumps(metadata or {}),
                },
            )
            # Touch the parent session so list ordering reflects recent activity.
            await s.execute(
                text(
                    "UPDATE chat_sessions SET updated_at = now() "
                    "WHERE id = :sid AND tenant_id = :t"
                ),
                {"sid": session_id, "t": tenant_id},
            )

    async def list_messages(
        self,
        session_id: str,
        tenant_id: str,
        *,
        limit: int = _DEFAULT_MESSAGE_LIMIT,
        before: str | None = None,
    ) -> list[dict[str, Any]]:
        """Return up to ``limit`` messages for a session in ascending
        (created_at, id) order for display.

        The newest ``limit`` messages are selected (index-friendly
        ``ORDER BY created_at DESC`` over idx_chat_messages_session_created) and
        returned oldest-first, so the query is bounded even for sessions with
        millions of messages. Pass ``before`` — an opaque
        ``"<created_at_iso>|<id>"`` cursor (the (created_at, id) of the oldest row
        already seen) — to page further back into history.
        """
        cursor = _decode_cursor(before)
        clause = ""
        params: dict[str, Any] = {"sid": session_id, "t": tenant_id, "limit": limit}
        if cursor is not None:
            clause = " AND (created_at, id) < (CAST(:before_ts AS timestamptz), :before_id)"
            params["before_ts"], params["before_id"] = cursor
        async with self._sf() as s, s.begin(), sqlalchemy_rls_context(s, tenant_id):
            rows = (
                await s.execute(
                    text(
                        "SELECT * FROM chat_messages "
                        "WHERE session_id = :sid AND tenant_id = :t"
                        f"{clause} "
                        "ORDER BY created_at DESC, id DESC LIMIT :limit"
                    ),
                    params,
                )
            ).mappings().all()
            # Selected newest-first for the LIMIT; return oldest-first for display.
            return [dict(r) for r in reversed(rows)]

    async def get_message(self, message_id: str, tenant_id: str) -> dict[str, Any] | None:
        async with self._sf() as s, s.begin(), sqlalchemy_rls_context(s, tenant_id):
            row = (
                await s.execute(
                    text("SELECT * FROM chat_messages WHERE id = :id AND tenant_id = :t"),
                    {"id": message_id, "t": tenant_id},
                )
            ).mappings().one_or_none()
            return dict(row) if row is not None else None

    async def update_message_content(
        self, message_id: str, tenant_id: str, content: str
    ) -> bool:
        async with self._sf() as s, s.begin(), sqlalchemy_rls_context(s, tenant_id):
            result = await s.execute(
                text("UPDATE chat_messages SET content = :c WHERE id = :id AND tenant_id = :t"),
                {"c": content, "id": message_id, "t": tenant_id},
            )
            return (result.rowcount or 0) > 0

    async def delete_messages_after(
        self, session_id: str, tenant_id: str, after_created_at: Any
    ) -> list[str]:
        """Delete (branch-prune) messages created strictly after a timestamp; return their ids."""
        async with self._sf() as s, s.begin(), sqlalchemy_rls_context(s, tenant_id):
            ids = [
                str(r["id"])
                for r in (
                    await s.execute(
                        text(
                            "SELECT id FROM chat_messages "
                            "WHERE session_id = :sid AND tenant_id = :t AND created_at > :ts"
                        ),
                        {"sid": session_id, "t": tenant_id, "ts": after_created_at},
                    )
                ).mappings().all()
            ]
            if ids:
                await s.execute(
                    text(
                        "DELETE FROM chat_messages "
                        "WHERE session_id = :sid AND tenant_id = :t AND created_at > :ts"
                    ),
                    {"sid": session_id, "t": tenant_id, "ts": after_created_at},
                )
            return ids

    async def search_messages(
        self,
        tenant_id: str,
        query: str,
        *,
        session_id: str | None = None,
        limit: int = 20,
    ) -> list[dict[str, Any]]:
        """Full-text search over the tenant's messages, newest first (ORG-32).

        Uses the ``idx_chat_messages_fts`` GIN index (``to_tsvector('english',
        content)``) with ``websearch_to_tsquery``, so it stays an index scan at
        millions of rows; bounded by ``limit``."""
        sql = (
            "SELECT * FROM chat_messages WHERE tenant_id = :t "
            "AND to_tsvector('english', content) @@ websearch_to_tsquery('english', :q)"
        )
        params: dict[str, Any] = {"t": tenant_id, "q": query, "lim": max(1, min(limit, 100))}
        if session_id is not None:
            sql += " AND session_id = :sid"
            params["sid"] = session_id
        sql += " ORDER BY created_at DESC, id DESC LIMIT :lim"
        async with self._sf() as s, s.begin(), sqlalchemy_rls_context(s, tenant_id):
            rows = (await s.execute(text(sql), params)).mappings().all()
            return [dict(r) for r in rows]

    async def count_messages(self, session_id: str, tenant_id: str) -> int:
        """How many messages the session holds (index scan on
        idx_chat_messages_session_created; one session's rows only)."""
        async with self._sf() as s, s.begin(), sqlalchemy_rls_context(s, tenant_id):
            value = (
                await s.execute(
                    text(
                        "SELECT count(*) FROM chat_messages "
                        "WHERE session_id = :sid AND tenant_id = :t"
                    ),
                    {"sid": session_id, "t": tenant_id},
                )
            ).scalar()
            return int(value or 0)

    # ── Artifacts (chat_artifacts, ORG-42) ────────────────────────────────────
    # Generated documents (kind='document', retention via expires_at) and saved
    # session artifacts (kind='snippet', deleted with their session). Every read
    # excludes expired rows, so a document past retention is gone even before the
    # purge task removes the row.

    async def put_artifact(
        self,
        *,
        artifact_id: str,
        tenant_id: str,
        kind: str,
        title: str,
        mime: str,
        content: bytes,
        language: str | None = None,
        session_id: str | None = None,
        message_id: str | None = None,
        expires_at: datetime | None = None,
    ) -> None:
        async with self._sf() as s, s.begin(), sqlalchemy_rls_context(s, tenant_id):
            await s.execute(
                text(
                    "INSERT INTO chat_artifacts "
                    "(id, tenant_id, kind, session_id, message_id, title, mime, language, "
                    "content, size_bytes, expires_at) "
                    "VALUES (:id, :t, :kind, :sid, :mid, :title, :mime, :lang, :c, :n, :exp)"
                ),
                {
                    "id": artifact_id,
                    "t": tenant_id,
                    "kind": kind,
                    "sid": session_id,
                    "mid": message_id,
                    "title": title,
                    "mime": mime,
                    "lang": language,
                    "c": content,
                    "n": len(content),
                    "exp": expires_at,
                },
            )

    async def get_artifact(
        self, artifact_id: str, tenant_id: str, *, kind: str | None = None
    ) -> dict[str, Any] | None:
        sql = (
            "SELECT * FROM chat_artifacts WHERE id = :id AND tenant_id = :t "
            "AND (expires_at IS NULL OR expires_at > now())"
        )
        params: dict[str, Any] = {"id": artifact_id, "t": tenant_id}
        if kind is not None:
            sql += " AND kind = :kind"
            params["kind"] = kind
        async with self._sf() as s, s.begin(), sqlalchemy_rls_context(s, tenant_id):
            row = (await s.execute(text(sql), params)).mappings().one_or_none()
            return dict(row) if row is not None else None

    async def list_session_artifacts(
        self, session_id: str, tenant_id: str, *, limit: int = 200
    ) -> list[dict[str, Any]]:
        """A session's saved artifacts, oldest first, bounded by ``limit``."""
        async with self._sf() as s, s.begin(), sqlalchemy_rls_context(s, tenant_id):
            rows = (
                (
                    await s.execute(
                        text(
                            "SELECT * FROM chat_artifacts "
                            "WHERE tenant_id = :t AND session_id = :sid AND kind = 'snippet' "
                            "ORDER BY created_at, id LIMIT :lim"
                        ),
                        {"t": tenant_id, "sid": session_id, "lim": max(1, min(limit, 1000))},
                    )
                )
                .mappings()
                .all()
            )
            return [dict(r) for r in rows]

    async def update_session_artifact(
        self, artifact_id: str, session_id: str, tenant_id: str, content: bytes
    ) -> dict[str, Any] | None:
        async with self._sf() as s, s.begin(), sqlalchemy_rls_context(s, tenant_id):
            row = (
                (
                    await s.execute(
                        text(
                            "UPDATE chat_artifacts SET content = :c, size_bytes = :n, "
                            "updated_at = now() "
                            "WHERE id = :id AND tenant_id = :t AND session_id = :sid "
                            "AND kind = 'snippet' RETURNING *"
                        ),
                        {
                            "c": content,
                            "n": len(content),
                            "id": artifact_id,
                            "t": tenant_id,
                            "sid": session_id,
                        },
                    )
                )
                .mappings()
                .one_or_none()
            )
            return dict(row) if row is not None else None

    async def delete_session_artifact(
        self, artifact_id: str, session_id: str, tenant_id: str
    ) -> bool:
        async with self._sf() as s, s.begin(), sqlalchemy_rls_context(s, tenant_id):
            result = await s.execute(
                text(
                    "DELETE FROM chat_artifacts "
                    "WHERE id = :id AND tenant_id = :t AND session_id = :sid "
                    "AND kind = 'snippet'"
                ),
                {"id": artifact_id, "t": tenant_id, "sid": session_id},
            )
            return (result.rowcount or 0) > 0
