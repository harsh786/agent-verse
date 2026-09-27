"""Durable stores for org custom roles and Universal Command Gateway history.

Both used to live in module-level dicts in ``app/org/router.py`` — commented
"swapped for DB in lifespan", which never happened. With more than one API
replica a role created on one replica did not exist on the others, a command
submitted on one could not be polled on another, and a restart erased both.
They are now rows in ``org_custom_roles`` / ``org_commands`` (migration
``d0e1f2a3b4c5``), tenant-isolated by FORCE'd RLS and, defense-in-depth, by an
explicit ``tenant_id`` predicate on every statement (the pattern of
``app/org/brain_store.py``).

When the app has no database (unit tests / in-memory dev mode) each store falls
back to a process-local dict keyed by ``(tenant_id, org_id)`` — never by org id
alone, so even the fallback cannot hand one tenant another tenant's rows.
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

from sqlalchemy import text as sa_text

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

__all__ = ["COMMAND_HISTORY_CAP", "OrgCommandStore", "OrgRoleStore"]

# Newest commands kept per org; older rows are trimmed on insert.
COMMAND_HISTORY_CAP = 200

_MEM_ROLES: dict[tuple[str, str], list[dict[str, Any]]] = {}
_MEM_COMMANDS: dict[tuple[str, str], list[dict[str, Any]]] = {}


@asynccontextmanager
async def _scoped(session_factory: Any, tenant_id: str) -> AsyncIterator[AsyncSession]:
    from app.db.rls import sqlalchemy_rls_context

    async with (
        session_factory() as session,
        session.begin(),
        sqlalchemy_rls_context(session, tenant_id),
    ):
        yield session


def _iso(value: Any) -> Any:
    return value.isoformat() if isinstance(value, datetime) else value


class OrgRoleStore:
    """Custom (non-built-in) roles of an organisation."""

    def __init__(self, session_factory: Any, tenant_id: str, org_id: str) -> None:
        self._db = session_factory
        self._tenant_id = str(tenant_id)
        self._org_id = str(org_id)

    @property
    def _key(self) -> tuple[str, str]:
        return (self._tenant_id, self._org_id)

    @staticmethod
    def _row(row: Any) -> dict[str, Any]:
        permissions = row["permissions"]
        if isinstance(permissions, str):
            permissions = json.loads(permissions)
        return {
            "id": str(row["id"]),
            "name": row["name"],
            "description": row["description"] or "",
            "permissions": permissions or [],
            "member_count": int(row["member_count"] or 0),
        }

    async def list(self) -> list[dict[str, Any]]:
        if self._db is None:
            return [dict(r) for r in _MEM_ROLES.get(self._key, [])]
        async with _scoped(self._db, self._tenant_id) as s:
            rows = (
                await s.execute(
                    sa_text(
                        "SELECT id, name, description, permissions, member_count "
                        "FROM org_custom_roles "
                        "WHERE tenant_id = CAST(:tid AS uuid) AND org_id = CAST(:oid AS uuid) "
                        "ORDER BY created_at, id"
                    ),
                    {"tid": self._tenant_id, "oid": self._org_id},
                )
            ).mappings().all()
        return [self._row(r) for r in rows]

    async def create(self, role: dict[str, Any]) -> dict[str, Any]:
        if self._db is None:
            _MEM_ROLES.setdefault(self._key, []).append(dict(role))
            return role
        async with _scoped(self._db, self._tenant_id) as s:
            await s.execute(
                sa_text(
                    "INSERT INTO org_custom_roles "
                    "(id, tenant_id, org_id, name, description, permissions, member_count) "
                    "VALUES (CAST(:id AS uuid), CAST(:tid AS uuid), CAST(:oid AS uuid), "
                    " :name, :description, CAST(:permissions AS jsonb), :member_count)"
                ),
                {
                    "id": role["id"],
                    "tid": self._tenant_id,
                    "oid": self._org_id,
                    "name": role["name"],
                    "description": role.get("description", ""),
                    "permissions": json.dumps(role.get("permissions", [])),
                    "member_count": int(role.get("member_count", 0)),
                },
            )
        return role

    async def update(self, role_id: str, role: dict[str, Any]) -> bool:
        if self._db is None:
            roles = _MEM_ROLES.get(self._key, [])
            for i, existing in enumerate(roles):
                if existing["id"] == role_id:
                    roles[i] = {**role, "id": role_id}
                    return True
            return False
        if not _is_uuid(role_id):
            return False
        async with _scoped(self._db, self._tenant_id) as s:
            result = await s.execute(
                sa_text(
                    "UPDATE org_custom_roles SET name = :name, description = :description, "
                    " permissions = CAST(:permissions AS jsonb), member_count = :member_count, "
                    " updated_at = now() "
                    "WHERE id = CAST(:id AS uuid) AND tenant_id = CAST(:tid AS uuid) "
                    " AND org_id = CAST(:oid AS uuid)"
                ),
                {
                    "id": role_id,
                    "tid": self._tenant_id,
                    "oid": self._org_id,
                    "name": role["name"],
                    "description": role.get("description", ""),
                    "permissions": json.dumps(role.get("permissions", [])),
                    "member_count": int(role.get("member_count", 0)),
                },
            )
        return bool(getattr(result, "rowcount", 0))

    async def delete(self, role_id: str) -> bool:
        if self._db is None:
            roles = _MEM_ROLES.get(self._key, [])
            kept = [r for r in roles if r["id"] != role_id]
            _MEM_ROLES[self._key] = kept
            return len(kept) != len(roles)
        if not _is_uuid(role_id):
            return False
        async with _scoped(self._db, self._tenant_id) as s:
            result = await s.execute(
                sa_text(
                    "DELETE FROM org_custom_roles "
                    "WHERE id = CAST(:id AS uuid) AND tenant_id = CAST(:tid AS uuid) "
                    " AND org_id = CAST(:oid AS uuid)"
                ),
                {"id": role_id, "tid": self._tenant_id, "oid": self._org_id},
            )
        return bool(getattr(result, "rowcount", 0))


class OrgCommandStore:
    """Universal Command Gateway command history of an organisation."""

    _COLUMNS = (
        "command_id, command, channel, org_id, tenant_id, status, requires_2fa, "
        "conversation_id, goal_id, error, result, submitted_at"
    )

    def __init__(self, session_factory: Any, tenant_id: str, org_id: str) -> None:
        self._db = session_factory
        self._tenant_id = str(tenant_id)
        self._org_id = str(org_id)

    @property
    def _key(self) -> tuple[str, str]:
        return (self._tenant_id, self._org_id)

    @staticmethod
    def _row(row: Any) -> dict[str, object]:
        record: dict[str, object] = {k: _iso(v) for k, v in dict(row).items()}
        for key in ("command_id", "org_id", "tenant_id"):
            record[key] = str(record[key])
        result = record.get("result")
        if isinstance(result, str):
            record["result"] = json.loads(result)
        if record.get("goal_id") is None:
            record.pop("goal_id", None)
        if record.get("error") is None:
            record.pop("error", None)
        return record

    async def add(self, record: dict[str, object]) -> None:
        if self._db is None:
            history = _MEM_COMMANDS.setdefault(self._key, [])
            history.insert(0, dict(record))
            del history[COMMAND_HISTORY_CAP:]
            return
        async with _scoped(self._db, self._tenant_id) as s:
            await s.execute(
                sa_text(
                    "INSERT INTO org_commands (command_id, tenant_id, org_id, command, channel, "
                    " status, requires_2fa, conversation_id, submitted_at) "
                    "VALUES (CAST(:cid AS uuid), CAST(:tid AS uuid), CAST(:oid AS uuid), "
                    " :command, :channel, :status, :requires_2fa, :conversation_id, :submitted_at)"
                ),
                {
                    "cid": str(record["command_id"]),
                    "tid": self._tenant_id,
                    "oid": self._org_id,
                    "command": str(record["command"]),
                    "channel": str(record["channel"]),
                    "status": str(record["status"]),
                    "requires_2fa": bool(record["requires_2fa"]),
                    "conversation_id": record.get("conversation_id"),
                    "submitted_at": datetime.now(UTC),
                },
            )
            # Keep the newest COMMAND_HISTORY_CAP per org. The (tenant, org,
            # submitted_at DESC) index serves the OFFSET probe.
            await s.execute(
                sa_text(
                    "DELETE FROM org_commands "
                    "WHERE tenant_id = CAST(:tid AS uuid) AND org_id = CAST(:oid AS uuid) "
                    " AND submitted_at < ("
                    "   SELECT submitted_at FROM org_commands "
                    "   WHERE tenant_id = CAST(:tid AS uuid) AND org_id = CAST(:oid AS uuid) "
                    "   ORDER BY submitted_at DESC OFFSET :cap LIMIT 1)"
                ),
                {"tid": self._tenant_id, "oid": self._org_id, "cap": COMMAND_HISTORY_CAP - 1},
            )

    async def update(self, command_id: str, **fields: object) -> None:
        """Set ``status`` / ``goal_id`` / ``error`` on one command."""
        allowed = {k: v for k, v in fields.items() if k in ("status", "goal_id", "error")}
        if not allowed:
            return
        if self._db is None:
            for cmd in _MEM_COMMANDS.get(self._key, []):
                if cmd.get("command_id") == command_id:
                    cmd.update(allowed)
                    return
            return
        assignments = ", ".join(f"{k} = :{k}" for k in allowed)
        async with _scoped(self._db, self._tenant_id) as s:
            await s.execute(
                sa_text(
                    f"UPDATE org_commands SET {assignments} "
                    "WHERE command_id = CAST(:cid AS uuid) AND tenant_id = CAST(:tid AS uuid) "
                    " AND org_id = CAST(:oid AS uuid)"
                ),
                {
                    **{k: (None if v is None else str(v)) for k, v in allowed.items()},
                    "cid": command_id,
                    "tid": self._tenant_id,
                    "oid": self._org_id,
                },
            )

    async def list(
        self, *, limit: int, channel: str | None = None
    ) -> tuple[list[dict[str, object]], int]:
        """Return ``(newest-first page, total matching)``."""
        if self._db is None:
            history = _MEM_COMMANDS.get(self._key, [])
            if channel:
                history = [c for c in history if c.get("channel") == channel]
            return [dict(c) for c in history[:limit]], len(history)
        where = "tenant_id = CAST(:tid AS uuid) AND org_id = CAST(:oid AS uuid)"
        params: dict[str, object] = {"tid": self._tenant_id, "oid": self._org_id}
        if channel:
            where += " AND channel = :channel"
            params["channel"] = channel
        async with _scoped(self._db, self._tenant_id) as s:
            rows = (
                await s.execute(
                    sa_text(
                        f"SELECT {self._COLUMNS} FROM org_commands WHERE {where} "
                        "ORDER BY submitted_at DESC, command_id LIMIT :limit"
                    ),
                    {**params, "limit": limit},
                )
            ).mappings().all()
            total = (
                await s.execute(
                    sa_text(f"SELECT count(*) FROM org_commands WHERE {where}"),
                    params,
                )
            ).scalar_one()
        return [self._row(r) for r in rows], int(total)

    async def get(self, command_id: str) -> dict[str, object] | None:
        if self._db is None:
            for cmd in _MEM_COMMANDS.get(self._key, []):
                if cmd.get("command_id") == command_id:
                    return dict(cmd)
            return None
        if not _is_uuid(command_id):
            return None
        async with _scoped(self._db, self._tenant_id) as s:
            row = (
                await s.execute(
                    sa_text(
                        f"SELECT {self._COLUMNS} FROM org_commands "
                        "WHERE command_id = CAST(:cid AS uuid) AND tenant_id = CAST(:tid AS uuid) "
                        " AND org_id = CAST(:oid AS uuid)"
                    ),
                    {"cid": command_id, "tid": self._tenant_id, "oid": self._org_id},
                )
            ).mappings().first()
        return self._row(row) if row is not None else None


def _is_uuid(value: str) -> bool:
    import uuid

    try:
        uuid.UUID(str(value))
    except (ValueError, TypeError):
        return False
    return True
