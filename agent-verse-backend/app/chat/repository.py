"""PostgresChatRepository — durable chat session + message persistence.

Backs the chat subsystem with the ``chat_sessions`` / ``chat_messages`` tables
(migration 0130) so conversations survive restarts, span workers, and support
long-delayed recall. Every query runs inside an RLS tenant context AND filters
by ``tenant_id`` explicitly (defence-in-depth: a superuser/bypass-RLS role would
otherwise see other tenants' rows).
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.db.rls import sqlalchemy_rls_context

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

    async def list_sessions(self, tenant_id: str) -> list[dict[str, Any]]:
        async with self._sf() as s, s.begin(), sqlalchemy_rls_context(s, tenant_id):
            rows = (
                await s.execute(
                    text(
                        "SELECT * FROM chat_sessions WHERE tenant_id = :t "
                        "ORDER BY updated_at DESC"
                    ),
                    {"t": tenant_id},
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

    async def list_messages(self, session_id: str, tenant_id: str) -> list[dict[str, Any]]:
        async with self._sf() as s, s.begin(), sqlalchemy_rls_context(s, tenant_id):
            rows = (
                await s.execute(
                    text(
                        "SELECT * FROM chat_messages "
                        "WHERE session_id = :sid AND tenant_id = :t ORDER BY created_at"
                    ),
                    {"sid": session_id, "t": tenant_id},
                )
            ).mappings().all()
            return [dict(r) for r in rows]
