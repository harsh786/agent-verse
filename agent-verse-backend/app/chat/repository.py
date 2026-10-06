"""PostgresChatRepository — durable chat session + message persistence.

Backs the chat subsystem with the ``chat_sessions`` / ``chat_messages`` tables
(migration 0130) so conversations survive restarts, span workers, and support
long-delayed recall. Every query runs inside an RLS tenant context AND filters
by ``tenant_id`` explicitly (defence-in-depth: a superuser/bypass-RLS role would
otherwise see other tenants' rows).

CHAT-SEC-1: every session/message/artifact query also takes a required
:class:`~app.chat.ownership.ChatScope`. A caller's scope adds an explicit
``owner_principal`` predicate to the SQL and sets the ``app.chat_principal`` GUC
that the restrictive owner policies enforce; ``SYSTEM_SCOPE`` is the explicit
choice of an already-authorized internal path.
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator, Sequence
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.chat.ownership import SYSTEM_SCOPE, ChatFolderNotFoundError, ChatScope
from app.db.rls import sqlalchemy_rls_context

# Bounded defaults so list queries never walk an unbounded history (must scale past
# millions of rows). The message default covers the largest in-process caller
# (conversation-context building requests up to 1000), so slicing callers see no
# regression while the SQL is now LIMIT-bounded and keyset-pageable.
_DEFAULT_MESSAGE_LIMIT = 1000
_DEFAULT_SESSION_LIMIT = 500
# A principal's folders (CHAT-D-1): the list is bounded, and so is how many one
# principal may create (the sidebar renders them all).
MAX_FOLDERS_PER_OWNER = 500

# CHAT-D-2: a reply's thumbs (-1/0/1) on the 1..5 scale of ``goal_feedback``,
# whose daily self-improvement pass turns a low rating with a comment into a
# lesson (process_feedback_batch: rating <= 2).
_GOAL_FEEDBACK_RATING = {-1: 1, 0: 3, 1: 5}


def goal_feedback_id(tenant_id: str, message_id: str, principal: str) -> str:
    """The ``goal_feedback`` row mirroring one person's feedback on one reply."""
    return uuid.uuid5(
        uuid.NAMESPACE_URL, f"chat_feedback:{tenant_id}:{message_id}:{principal}"
    ).hex


class ChatFeedbackTargetError(ValueError):
    """Feedback is given on assistant replies only."""


# The folder a session is filed into must belong to the session's own owner.
_FOLDER_OF_OWNER = (
    "SELECT 1 FROM chat_session_folders WHERE id = :fid AND tenant_id = :t "
    "AND owner_principal IS NOT DISTINCT FROM :fowner FOR KEY SHARE"
)


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

    @asynccontextmanager
    async def _tx(self, tenant_id: str, scope: ChatScope) -> AsyncIterator[AsyncSession]:
        """One transaction under the tenant's RLS context and the caller's chat scope."""
        async with self._sf() as s, s.begin(), sqlalchemy_rls_context(s, tenant_id):
            if scope.guc:
                await s.execute(
                    text("SELECT set_config('app.chat_principal', :p, true)"),
                    {"p": scope.guc},
                )
            yield s

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
        owner_principal: str | None = None,
    ) -> None:
        scope = ChatScope.of(owner_principal) if owner_principal else ChatScope("unowned")
        async with self._tx(tenant_id, scope) as s:
            if folder_id is not None and (
                await s.execute(
                    text(_FOLDER_OF_OWNER),
                    {"fid": folder_id, "t": tenant_id, "fowner": owner_principal},
                )
            ).fetchone() is None:
                raise ChatFolderNotFoundError(folder_id)
            await s.execute(
                text(
                    "INSERT INTO chat_sessions (id, tenant_id, title, system_prompt, "
                    "agent_id, folder_id, owner_user_id, owner_principal) "
                    "VALUES (:id, :t, :title, :sp, :aid, :fid, :owner, :principal)"
                ),
                {
                    "id": session_id,
                    "t": tenant_id,
                    "title": title,
                    "sp": system_prompt,
                    "aid": agent_id,
                    "fid": folder_id,
                    "owner": owner_user_id,
                    "principal": owner_principal,
                },
            )

    async def get_session(
        self, session_id: str, tenant_id: str, *, scope: ChatScope
    ) -> dict[str, Any] | None:
        pred, pp = scope.predicate()
        async with self._tx(tenant_id, scope) as s:
            row = (
                await s.execute(
                    text(f"SELECT * FROM chat_sessions WHERE id = :id AND tenant_id = :t{pred}"),
                    {"id": session_id, "t": tenant_id, **pp},
                )
            ).mappings().one_or_none()
            return dict(row) if row is not None else None

    async def list_sessions(
        self,
        tenant_id: str,
        *,
        scope: ChatScope,
        limit: int = _DEFAULT_SESSION_LIMIT,
        before: str | None = None,
    ) -> list[dict[str, Any]]:
        """Return up to ``limit`` sessions, newest first.

        Keyset pagination: pass ``before`` — an opaque ``"<updated_at_iso>|<id>"``
        cursor (the (updated_at, id) of the last row already seen) — to page into
        older sessions. Bounded by ``limit`` so a tenant with millions of sessions
        never triggers a full-table walk. Keeps the index-friendly
        ``ORDER BY updated_at DESC`` (ix_chat_sessions_owner_keyset for a caller's
        own sessions).
        """
        cursor = _decode_cursor(before)
        clause, pp = scope.predicate()
        params: dict[str, Any] = {"t": tenant_id, "limit": limit, **pp}
        if cursor is not None:
            clause += " AND (updated_at, id) < (CAST(:before_ts AS timestamptz), :before_id)"
            params["before_ts"], params["before_id"] = cursor
        async with self._tx(tenant_id, scope) as s:
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

    async def update_session(
        self, session_id: str, tenant_id: str, *, scope: ChatScope, **fields: Any
    ) -> bool:
        updates = {k: v for k, v in fields.items() if k in _SESSION_UPDATABLE}
        if not updates:
            return False
        set_clause = ", ".join(f"{col} = :{col}" for col in updates)
        pred, pp = scope.predicate()
        async with self._tx(tenant_id, scope) as s:
            if updates.get("folder_id") is not None:
                # CHAT-D-1: file only into a folder of the session's own owner.
                owner = (
                    await s.execute(
                        text(
                            "SELECT owner_principal FROM chat_sessions WHERE id = :id "
                            f"AND tenant_id = :t{pred} FOR UPDATE"
                        ),
                        {"id": session_id, "t": tenant_id, **pp},
                    )
                ).fetchone()
                if owner is None:
                    return False
                if (
                    await s.execute(
                        text(_FOLDER_OF_OWNER),
                        {"fid": updates["folder_id"], "t": tenant_id, "fowner": owner[0]},
                    )
                ).fetchone() is None:
                    raise ChatFolderNotFoundError(str(updates["folder_id"]))
            result = await s.execute(
                text(
                    f"UPDATE chat_sessions SET {set_clause}, updated_at = now() "
                    f"WHERE id = :id AND tenant_id = :t{pred}"
                ),
                {**updates, "id": session_id, "t": tenant_id, **pp},
            )
            return (result.rowcount or 0) > 0

    # ── Folders (chat_session_folders, CHAT-D-1) ──────────────────────────────
    # Owned like sessions: every query carries the owner predicate of the scope
    # and the restrictive ``chat_session_folders_owner`` policy enforces it.

    async def create_folder(
        self, *, folder_id: str, tenant_id: str, name: str, color: str, scope: ChatScope
    ) -> dict[str, Any] | None:
        """Create a folder owned by the scope's principal; None at the per-owner cap."""
        if scope.kind != "principal":
            raise ValueError("a chat folder is created by a principal")
        params = {
            "id": folder_id, "t": tenant_id, "name": name, "color": color,
            "p": scope.principal, "max": MAX_FOLDERS_PER_OWNER,
        }
        async with self._tx(tenant_id, scope) as s:
            row = (
                await s.execute(
                    text(
                        "INSERT INTO chat_session_folders "
                        "(id, tenant_id, name, color, owner_principal, position) "
                        "SELECT CAST(:id AS varchar), CAST(:t AS varchar), "
                        "CAST(:name AS varchar), CAST(:color AS varchar), "
                        "CAST(:p AS varchar), "
                        "coalesce((SELECT max(position) + 1 FROM chat_session_folders "
                        "WHERE tenant_id = :t AND owner_principal = :p), 0) "
                        "WHERE (SELECT count(*) FROM chat_session_folders "
                        "WHERE tenant_id = :t AND owner_principal = :p) < :max "
                        "RETURNING *"
                    ),
                    params,
                )
            ).mappings().one_or_none()
            return dict(row) if row is not None else None

    async def list_folders(
        self, tenant_id: str, *, scope: ChatScope, limit: int = MAX_FOLDERS_PER_OWNER
    ) -> list[dict[str, Any]]:
        pred, pp = scope.predicate()
        async with self._tx(tenant_id, scope) as s:
            rows = (
                await s.execute(
                    text(
                        f"SELECT * FROM chat_session_folders WHERE tenant_id = :t{pred} "
                        "ORDER BY position, created_at, id LIMIT :lim"
                    ),
                    {"t": tenant_id, "lim": max(1, min(limit, MAX_FOLDERS_PER_OWNER)), **pp},
                )
            ).mappings().all()
            return [dict(r) for r in rows]

    async def update_folder(
        self,
        folder_id: str,
        tenant_id: str,
        *,
        scope: ChatScope,
        name: str | None = None,
        color: str | None = None,
    ) -> dict[str, Any] | None:
        """Rename/recolor a folder of the scope; None when it is not the caller's."""
        pred, pp = scope.predicate()
        async with self._tx(tenant_id, scope) as s:
            row = (
                await s.execute(
                    text(
                        "UPDATE chat_session_folders SET name = coalesce(:name, name), "
                        "color = coalesce(:color, color), updated_at = now() "
                        f"WHERE id = :id AND tenant_id = :t{pred} RETURNING *"
                    ),
                    {"name": name, "color": color, "id": folder_id, "t": tenant_id, **pp},
                )
            ).mappings().one_or_none()
            return dict(row) if row is not None else None

    async def delete_folder(self, folder_id: str, tenant_id: str, *, scope: ChatScope) -> bool:
        """Delete a folder of the scope and unfile its sessions, in one transaction.

        The DELETE waits for any filing that holds the folder row (``FOR KEY
        SHARE``), and the unfiling statement runs after it, so a session filed
        concurrently is unfiled too (idx_chat_sessions_folder). The sessions
        keep their ``updated_at``: unfiling is not chat activity.
        """
        pred, pp = scope.predicate()
        async with self._tx(tenant_id, scope) as s:
            owner = (
                await s.execute(
                    text(
                        "DELETE FROM chat_session_folders WHERE id = :id AND tenant_id = :t"
                        f"{pred} RETURNING owner_principal"
                    ),
                    {"id": folder_id, "t": tenant_id, **pp},
                )
            ).fetchone()
            if owner is None:
                return False
            await s.execute(
                text(
                    "UPDATE chat_sessions SET folder_id = NULL WHERE folder_id = :id "
                    "AND tenant_id = :t AND owner_principal IS NOT DISTINCT FROM :owner"
                ),
                {"id": folder_id, "t": tenant_id, "owner": owner[0]},
            )
            return True

    async def assign_unowned_session(
        self, session_id: str, tenant_id: str, *, owner_user_id: str
    ) -> bool:
        """Give a session with no owner to a person (admin route, audited by the caller).

        Only an unowned session is ever reassigned (the predicate and the
        unowned scope both require ``owner_principal IS NULL``), so an owned
        session can never be taken over this way.
        """
        # No owner GUC: the row leaves the unowned scope by this very UPDATE, so
        # the explicit ``owner_principal IS NULL`` predicate is the guard.
        async with self._tx(tenant_id, SYSTEM_SCOPE) as s:
            result = await s.execute(
                text(
                    "UPDATE chat_sessions SET owner_principal = :p, owner_user_id = :u, "
                    "updated_at = now() WHERE id = :id AND tenant_id = :t "
                    "AND owner_principal IS NULL"
                ),
                {"p": f"user:{owner_user_id}", "u": owner_user_id, "id": session_id,
                 "t": tenant_id},
            )
            return (result.rowcount or 0) > 0

    async def delete_session(self, session_id: str, tenant_id: str, *, scope: ChatScope) -> bool:
        pred, pp = scope.predicate()
        params = {"id": session_id, "t": tenant_id, **pp}
        async with self._tx(tenant_id, scope) as s:
            # Lock the session row under the owner predicate first, so a message
            # of another principal's session is never deleted.
            owned = (
                await s.execute(
                    text(
                        "SELECT id FROM chat_sessions WHERE id = :id AND tenant_id = :t"
                        f"{pred} FOR UPDATE"
                    ),
                    params,
                )
            ).fetchone()
            if owned is None:
                return False
            await s.execute(
                text("DELETE FROM chat_messages WHERE session_id = :id AND tenant_id = :t"),
                {"id": session_id, "t": tenant_id},
            )
            result = await s.execute(
                text(f"DELETE FROM chat_sessions WHERE id = :id AND tenant_id = :t{pred}"),
                params,
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
        scope: ChatScope,
    ) -> None:
        import json

        async with self._tx(tenant_id, scope) as s:
            if scope.kind != "system":
                pred, pp = scope.predicate()
                owned = (
                    await s.execute(
                        text(
                            "SELECT 1 FROM chat_sessions WHERE id = :sid AND tenant_id = :t"
                            f"{pred}"
                        ),
                        {"sid": session_id, "t": tenant_id, **pp},
                    )
                ).fetchone()
                if owned is None:
                    raise LookupError(f"chat session {session_id} not found")
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
        scope: ChatScope,
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
        clause, pp = scope.session_exists("chat_messages.session_id")
        params: dict[str, Any] = {"sid": session_id, "t": tenant_id, "limit": limit, **pp}
        if cursor is not None:
            clause += " AND (created_at, id) < (CAST(:before_ts AS timestamptz), :before_id)"
            params["before_ts"], params["before_id"] = cursor
        async with self._tx(tenant_id, scope) as s:
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

    async def get_message(
        self, message_id: str, tenant_id: str, *, scope: ChatScope
    ) -> dict[str, Any] | None:
        own, pp = scope.session_exists("chat_messages.session_id")
        async with self._tx(tenant_id, scope) as s:
            row = (
                await s.execute(
                    text(f"SELECT * FROM chat_messages WHERE id = :id AND tenant_id = :t{own}"),
                    {"id": message_id, "t": tenant_id, **pp},
                )
            ).mappings().one_or_none()
            return dict(row) if row is not None else None

    async def update_message_content(
        self, message_id: str, tenant_id: str, content: str, *, scope: ChatScope
    ) -> bool:
        own, pp = scope.session_exists("chat_messages.session_id")
        async with self._tx(tenant_id, scope) as s:
            result = await s.execute(
                text(
                    "UPDATE chat_messages SET content = :c WHERE id = :id AND tenant_id = :t"
                    f"{own}"
                ),
                {"c": content, "id": message_id, "t": tenant_id, **pp},
            )
            if not result.rowcount:
                return False
            # The session changed: its transcript (CHAT-KB) is re-read by the
            # next sync, whose cursor walks sessions by updated_at.
            await s.execute(
                text(
                    "UPDATE chat_sessions SET updated_at = now() WHERE tenant_id = :t "
                    "AND id = (SELECT session_id FROM chat_messages "
                    "WHERE id = :id AND tenant_id = :t)"
                ),
                {"id": message_id, "t": tenant_id},
            )
            return (result.rowcount or 0) > 0

    async def delete_messages_after(
        self, session_id: str, tenant_id: str, after_created_at: Any, *, scope: ChatScope
    ) -> list[str]:
        """Delete (branch-prune) messages created strictly after a timestamp; return their ids."""
        own, pp = scope.session_exists("chat_messages.session_id")
        params = {"sid": session_id, "t": tenant_id, "ts": after_created_at, **pp}
        async with self._tx(tenant_id, scope) as s:
            rows = (
                await s.execute(
                    text(
                        "DELETE FROM chat_messages "
                        "WHERE session_id = :sid AND tenant_id = :t AND created_at > :ts"
                        f"{own} RETURNING id"
                    ),
                    params,
                )
            ).fetchall()
            return [str(r[0]) for r in rows]

    async def delete_message(
        self, session_id: str, message_id: str, tenant_id: str, *, scope: ChatScope
    ) -> bool:
        """Hard-delete one message of a session (CHAT-SEC-2); bumps the session.

        The row is removed from the table (not hidden), and the session's
        ``updated_at`` moves so its transcript's next sync re-reads it.
        """
        own, pp = scope.session_exists("chat_messages.session_id")
        params = {"id": message_id, "sid": session_id, "t": tenant_id, **pp}
        async with self._tx(tenant_id, scope) as s:
            result = await s.execute(
                text(
                    "DELETE FROM chat_messages WHERE id = :id AND session_id = :sid "
                    f"AND tenant_id = :t{own}"
                ),
                params,
            )
            if not result.rowcount:
                return False
            await s.execute(
                text(
                    "UPDATE chat_sessions SET updated_at = now() "
                    "WHERE id = :sid AND tenant_id = :t"
                ),
                {"sid": session_id, "t": tenant_id},
            )
            return True

    async def search_messages(
        self,
        tenant_id: str,
        query: str,
        *,
        scope: ChatScope,
        session_id: str | None = None,
        limit: int = 20,
    ) -> list[dict[str, Any]]:
        """Full-text search over the tenant's messages, newest first (ORG-32).

        Uses the ``idx_chat_messages_fts`` GIN index (``to_tsvector('english',
        content)``) with ``websearch_to_tsquery``, so it stays an index scan at
        millions of rows; bounded by ``limit``. A caller's scope keeps only the
        messages of that caller's own sessions."""
        own, pp = scope.session_exists("chat_messages.session_id")
        sql = (
            "SELECT * FROM chat_messages WHERE tenant_id = :t "
            "AND to_tsvector('english', content) @@ websearch_to_tsquery('english', :q)"
            f"{own}"
        )
        params: dict[str, Any] = {
            "t": tenant_id, "q": query, "lim": max(1, min(limit, 100)), **pp,
        }
        if session_id is not None:
            sql += " AND session_id = :sid"
            params["sid"] = session_id
        sql += " ORDER BY created_at DESC, id DESC LIMIT :lim"
        async with self._tx(tenant_id, scope) as s:
            rows = (await s.execute(text(sql), params)).mappings().all()
            return [dict(r) for r in rows]

    async def count_messages(self, session_id: str, tenant_id: str, *, scope: ChatScope) -> int:
        """How many messages the session holds (index scan on
        idx_chat_messages_session_created; one session's rows only)."""
        own, pp = scope.session_exists("chat_messages.session_id")
        async with self._tx(tenant_id, scope) as s:
            value = (
                await s.execute(
                    text(
                        "SELECT count(*) FROM chat_messages "
                        f"WHERE session_id = :sid AND tenant_id = :t{own}"
                    ),
                    {"sid": session_id, "t": tenant_id, **pp},
                )
            ).scalar()
            return int(value or 0)

    # ── Feedback (chat_message_feedback, CHAT-D-2) ────────────────────────────
    # One row per (message, rater). The rater is the scope's principal, who owns
    # the session (chats are private), so every query carries both the owner
    # predicate of the session and the rater's own.

    async def upsert_feedback(
        self,
        *,
        tenant_id: str,
        session_id: str,
        message_id: str,
        rating: int,
        comment: str | None,
        scope: ChatScope,
    ) -> dict[str, Any] | None:
        """Save (or edit) the caller's feedback on a reply; None when not visible.

        When the reply carries a goal, the same transaction mirrors it into
        ``goal_feedback`` (deterministic id, upserted and re-queued for the
        self-improvement pass), so an edit never adds a second signal.
        """
        if scope.kind != "principal":
            raise ValueError("feedback is given by a principal")
        own, pp = scope.session_exists("chat_messages.session_id")
        async with self._tx(tenant_id, scope) as s:
            msg = (
                await s.execute(
                    text(
                        "SELECT role, goal_id FROM chat_messages WHERE id = :mid "
                        f"AND session_id = :sid AND tenant_id = :t{own} FOR KEY SHARE"
                    ),
                    {"mid": message_id, "sid": session_id, "t": tenant_id, **pp},
                )
            ).fetchone()
            if msg is None:
                return None
            if msg[0] != "assistant":
                raise ChatFeedbackTargetError("feedback is given on assistant replies only")
            row = (
                await s.execute(
                    text(
                        "INSERT INTO chat_message_feedback "
                        "(id, tenant_id, session_id, message_id, owner_principal, rating, comment) "
                        "VALUES (:id, :t, :sid, :mid, :p, :r, :c) "
                        "ON CONFLICT (message_id, owner_principal) DO UPDATE SET "
                        "rating = EXCLUDED.rating, comment = EXCLUDED.comment, "
                        "updated_at = now() RETURNING *"
                    ),
                    {
                        "id": uuid.uuid4().hex, "t": tenant_id, "sid": session_id,
                        "mid": message_id, "p": scope.principal, "r": rating, "c": comment,
                    },
                )
            ).mappings().one()
            if msg[1]:
                await s.execute(
                    text(
                        "INSERT INTO goal_feedback "
                        "(id, goal_id, tenant_id, rating, correction, created_at) "
                        "SELECT CAST(:id AS varchar), CAST(:gid AS varchar), "
                        "CAST(:t AS varchar), CAST(:r AS smallint), CAST(:c AS text), now() "
                        "WHERE EXISTS (SELECT 1 FROM goals WHERE id = :gid AND tenant_id = :t) "
                        "ON CONFLICT (id) DO UPDATE SET rating = EXCLUDED.rating, "
                        "correction = EXCLUDED.correction, processed_at = NULL"
                    ),
                    {
                        "id": goal_feedback_id(tenant_id, message_id, str(scope.principal)),
                        "gid": msg[1], "t": tenant_id,
                        "r": _GOAL_FEEDBACK_RATING[rating], "c": comment,
                    },
                )
            return dict(row)

    async def delete_feedback(
        self, *, tenant_id: str, session_id: str, message_id: str, scope: ChatScope
    ) -> bool:
        """Clear the caller's feedback on a reply (and its goal_feedback mirror)."""
        if scope.kind != "principal":
            raise ValueError("feedback is given by a principal")
        own, pp = scope.session_exists("chat_message_feedback.session_id")
        async with self._tx(tenant_id, scope) as s:
            gone = (
                await s.execute(
                    text(
                        "DELETE FROM chat_message_feedback WHERE message_id = :mid "
                        "AND session_id = :sid AND tenant_id = :t AND owner_principal = :p"
                        f"{own} RETURNING id"
                    ),
                    {
                        "mid": message_id, "sid": session_id, "t": tenant_id,
                        "p": scope.principal, **pp,
                    },
                )
            ).fetchone()
            if gone is None:
                return False
            await s.execute(
                text("DELETE FROM goal_feedback WHERE id = :id AND tenant_id = :t"),
                {"id": goal_feedback_id(tenant_id, message_id, str(scope.principal)),
                 "t": tenant_id},
            )
            return True

    async def list_feedback(
        self,
        *,
        tenant_id: str,
        session_id: str,
        message_ids: Sequence[str],
        scope: ChatScope,
    ) -> list[dict[str, Any]]:
        """The caller's feedback on the given messages of a session (unique-index lookups)."""
        if scope.kind != "principal" or not message_ids:
            return []
        own, pp = scope.session_exists("chat_message_feedback.session_id")
        async with self._tx(tenant_id, scope) as s:
            rows = (
                await s.execute(
                    text(
                        "SELECT * FROM chat_message_feedback "
                        "WHERE message_id = ANY(CAST(:mids AS varchar[])) "
                        "AND owner_principal = :p AND session_id = :sid AND tenant_id = :t"
                        f"{own}"
                    ),
                    {
                        "mids": list(message_ids), "p": scope.principal, "sid": session_id,
                        "t": tenant_id, **pp,
                    },
                )
            ).mappings().all()
            return [dict(r) for r in rows]

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
        scope: ChatScope,
    ) -> None:
        async with self._tx(tenant_id, scope) as s:
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
        self, artifact_id: str, tenant_id: str, *, scope: ChatScope, kind: str | None = None
    ) -> dict[str, Any] | None:
        """One live artifact; one tied to a session only within that session's scope."""
        own, pp = scope.session_exists("chat_artifacts.session_id")
        sql = (
            "SELECT * FROM chat_artifacts WHERE id = :id AND tenant_id = :t "
            "AND (expires_at IS NULL OR expires_at > now())"
            + (f" AND (session_id IS NULL OR (TRUE{own}))" if own else "")
        )
        params: dict[str, Any] = {"id": artifact_id, "t": tenant_id, **pp}
        if kind is not None:
            sql += " AND kind = :kind"
            params["kind"] = kind
        async with self._tx(tenant_id, scope) as s:
            row = (await s.execute(text(sql), params)).mappings().one_or_none()
            return dict(row) if row is not None else None

    async def list_session_artifacts(
        self, session_id: str, tenant_id: str, *, scope: ChatScope, limit: int = 200
    ) -> list[dict[str, Any]]:
        """A session's saved artifacts, oldest first, bounded by ``limit``."""
        own, pp = scope.session_exists("chat_artifacts.session_id")
        async with self._tx(tenant_id, scope) as s:
            rows = (
                (
                    await s.execute(
                        text(
                            "SELECT * FROM chat_artifacts "
                            "WHERE tenant_id = :t AND session_id = :sid AND kind = 'snippet'"
                            f"{own} ORDER BY created_at, id LIMIT :lim"
                        ),
                        {
                            "t": tenant_id, "sid": session_id,
                            "lim": max(1, min(limit, 1000)), **pp,
                        },
                    )
                )
                .mappings()
                .all()
            )
            return [dict(r) for r in rows]

    async def update_session_artifact(
        self,
        artifact_id: str,
        session_id: str,
        tenant_id: str,
        content: bytes,
        *,
        scope: ChatScope,
    ) -> dict[str, Any] | None:
        own, pp = scope.session_exists("chat_artifacts.session_id")
        async with self._tx(tenant_id, scope) as s:
            row = (
                (
                    await s.execute(
                        text(
                            "UPDATE chat_artifacts SET content = :c, size_bytes = :n, "
                            "updated_at = now() "
                            "WHERE id = :id AND tenant_id = :t AND session_id = :sid "
                            f"AND kind = 'snippet'{own} RETURNING *"
                        ),
                        {
                            "c": content,
                            "n": len(content),
                            "id": artifact_id,
                            "t": tenant_id,
                            "sid": session_id,
                            **pp,
                        },
                    )
                )
                .mappings()
                .one_or_none()
            )
            return dict(row) if row is not None else None

    async def delete_session_artifact(
        self, artifact_id: str, session_id: str, tenant_id: str, *, scope: ChatScope
    ) -> bool:
        own, pp = scope.session_exists("chat_artifacts.session_id")
        async with self._tx(tenant_id, scope) as s:
            result = await s.execute(
                text(
                    "DELETE FROM chat_artifacts "
                    "WHERE id = :id AND tenant_id = :t AND session_id = :sid "
                    f"AND kind = 'snippet'{own}"
                ),
                {"id": artifact_id, "t": tenant_id, "sid": session_id, **pp},
            )
            return (result.rowcount or 0) > 0
