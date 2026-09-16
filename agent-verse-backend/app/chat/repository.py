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
    ) -> None:
        async with self._sf() as s, s.begin(), sqlalchemy_rls_context(s, tenant_id):
            await s.execute(
                text(
                    "INSERT INTO chat_sessions "
                    "(id, tenant_id, title, system_prompt, agent_id, folder_id) "
                    "VALUES (:id, :t, :title, :sp, :aid, :fid)"
                ),
                {
                    "id": session_id,
                    "t": tenant_id,
                    "title": title,
                    "sp": system_prompt,
                    "aid": agent_id,
                    "fid": folder_id,
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
