"""Postgres persistence for the connector registry (MCPREG-01).

Postgres (``mcp_servers``, ``mcp_builtin_provisioning``; migration a7c4e2f9d1b3)
is the source of truth for every tenant's connectors. Redis holds only a
short-TTL read cache, so a FLUSHALL or an eviction loses nothing.

Every statement runs inside ``sqlalchemy_rls_context`` AND carries an explicit
``tenant_id = :t`` predicate (RLS binds only a least-privilege role).

The registry used to keep connectors in Redis only (``mcp:servers:{t}:{id}``,
``mcp:server_ids:{t}``, ``mcp:builtins_provisioned:{t}``). Those keys are
copied here once (``app.mcp.connector_backfill``); until that copy has been
recorded as complete, a Postgres miss falls back to the legacy Redis entry and
copies it (read-repair), so no connector disappears during the deploy window.
Legacy keys are never deleted by the copy.
"""

from __future__ import annotations

import json
import time
from collections.abc import Callable
from typing import Any

from app.db.rls import sqlalchemy_rls_context

LEGACY_SERVER_KEY = "mcp:servers:{tenant}:{server}"
LEGACY_INDEX_KEY = "mcp:server_ids:{tenant}"
LEGACY_BUILTINS_MARKER_KEY = "mcp:builtins_provisioned:{tenant}"
CACHE_KEY = "mcp:cfgcache:v1:{tenant}:{server}"
BACKFILL_NAME = "connector_redis_to_postgres_v1"
_NAME_UNIQUE_INDEX = "uq_mcp_servers_tenant_name_key"
_PAGE_SIZE = 500


class ConnectorConflictError(Exception):
    """A connector with this id (``kind="id"``) or name (``kind="name"``) exists."""

    def __init__(self, kind: str, detail: str) -> None:
        super().__init__(detail)
        self.kind = kind


class ConnectorStoreUnavailableError(RuntimeError):
    """The durable connector store could not be read or written (fail closed)."""


def name_key(name: str) -> str:
    """Display names are unique per tenant, compared casefolded and space-normalised."""
    return " ".join(str(name).split()).casefold()[:200]


def _is_name_conflict(exc: BaseException) -> bool:
    return _NAME_UNIQUE_INDEX in str(exc)


def _is_unique_violation(exc: BaseException) -> bool:
    from sqlalchemy.exc import IntegrityError

    return isinstance(exc, IntegrityError)


def _decode(raw: Any) -> str | None:
    if raw is None:
        return None
    return raw.decode() if isinstance(raw, bytes) else str(raw)


def _as_dict(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return value
    parsed = json.loads(value)
    return parsed if isinstance(parsed, dict) else {}


def _row_params(tenant_id: str, server_id: str, config: dict[str, Any]) -> dict[str, Any]:
    name = str(config.get("name") or server_id)[:200]
    return {
        "t": tenant_id,
        "id": server_id,
        "name": name,
        "nk": name_key(name),
        "url": str(config.get("url") or config.get("base_url") or ""),
        "auth": str(config.get("auth_type") or "none")[:50],
        "descr": str(config.get("description") or ""),
        "prio": int(config.get("priority") or 0),
        "status": str(config.get("status") or "active")[:20],
        "enabled": bool(config.get("enabled", True)),
        "bt": str(config.get("builtin_type") or "")[:255],
        "cfg": json.dumps(config),
    }


_COLUMNS = (
    "tenant_id, id, name, name_key, url, auth_type, description, priority, status, "
    "enabled, builtin_type, config"
)
_VALUES = (
    ":t, :id, :name, :nk, :url, :auth, :descr, :prio, :status, :enabled, :bt, CAST(:cfg AS jsonb)"
)
_SET = (
    "name = EXCLUDED.name, name_key = EXCLUDED.name_key, url = EXCLUDED.url, "
    "auth_type = EXCLUDED.auth_type, description = EXCLUDED.description, "
    "priority = EXCLUDED.priority, status = EXCLUDED.status, enabled = EXCLUDED.enabled, "
    "builtin_type = EXCLUDED.builtin_type, config = EXCLUDED.config, updated_at = NOW()"
)


class PostgresConnectorRows:
    """Tenant-scoped CRUD over ``mcp_servers`` (+ the built-in marker)."""

    def __init__(self, db_factory: Callable[[], Any]) -> None:
        self._db = db_factory

    def _tx(self, session: Any, tenant_id: str) -> Any:
        return sqlalchemy_rls_context(session, tenant_id)

    async def get(self, tenant_id: str, server_id: str) -> dict[str, Any] | None:
        from sqlalchemy import text

        try:
            async with self._db() as s, s.begin(), self._tx(s, tenant_id):
                row = (
                    await s.execute(
                        text("SELECT config FROM mcp_servers WHERE tenant_id = :t AND id = :id"),
                        {"t": tenant_id, "id": server_id},
                    )
                ).fetchone()
        except Exception as exc:
            raise ConnectorStoreUnavailableError(f"connector read failed: {exc}") from exc
        return _as_dict(row[0]) if row is not None else None

    async def list_page(
        self, tenant_id: str, *, limit: int = _PAGE_SIZE, after: str | None = None
    ) -> list[tuple[str, dict[str, Any]]]:
        """Keyset page ordered by id (``after`` = last id of the previous page)."""
        from sqlalchemy import text

        try:
            async with self._db() as s, s.begin(), self._tx(s, tenant_id):
                rows = (
                    await s.execute(
                        text(
                            "SELECT id, config FROM mcp_servers WHERE tenant_id = :t "
                            "AND (CAST(:after AS VARCHAR) IS NULL OR id > :after) "
                            "ORDER BY id LIMIT :lim"
                        ),
                        {"t": tenant_id, "after": after, "lim": int(limit)},
                    )
                ).fetchall()
        except Exception as exc:
            raise ConnectorStoreUnavailableError(f"connector list failed: {exc}") from exc
        return [(str(r[0]), _as_dict(r[1])) for r in rows]

    async def list_all(self, tenant_id: str) -> list[tuple[str, dict[str, Any]]]:
        out: list[tuple[str, dict[str, Any]]] = []
        after: str | None = None
        while True:
            page = await self.list_page(tenant_id, limit=_PAGE_SIZE, after=after)
            out.extend(page)
            if len(page) < _PAGE_SIZE:
                return out
            after = page[-1][0]

    async def write(
        self,
        tenant_id: str,
        server_id: str,
        config: dict[str, Any],
        *,
        mode: str,
    ) -> bool:
        """``mode``: "upsert" | "insert" (conflict raises) | "update" (False if absent)
        | "insert_missing" (ON CONFLICT DO NOTHING; False when nothing was inserted)."""
        from sqlalchemy import text

        params = _row_params(tenant_id, server_id, config)
        if mode == "upsert":
            sql = (
                f"INSERT INTO mcp_servers ({_COLUMNS}) VALUES ({_VALUES}) "
                f"ON CONFLICT (tenant_id, id) DO UPDATE SET {_SET}"
            )
        elif mode == "insert":
            sql = f"INSERT INTO mcp_servers ({_COLUMNS}) VALUES ({_VALUES})"
        elif mode == "insert_missing":
            sql = f"INSERT INTO mcp_servers ({_COLUMNS}) VALUES ({_VALUES}) ON CONFLICT DO NOTHING"
        elif mode == "update":
            sql = (
                "UPDATE mcp_servers SET name = :name, name_key = :nk, url = :url, "
                "auth_type = :auth, description = :descr, priority = :prio, "
                "status = :status, enabled = :enabled, builtin_type = :bt, "
                "config = CAST(:cfg AS jsonb), updated_at = NOW() "
                "WHERE tenant_id = :t AND id = :id"
            )
        else:  # pragma: no cover - programming error
            raise ValueError(f"unknown write mode {mode!r}")
        try:
            async with self._db() as s, s.begin(), self._tx(s, tenant_id):
                result = await s.execute(text(sql), params)
        except Exception as exc:
            if _is_unique_violation(exc):
                if _is_name_conflict(exc):
                    raise ConnectorConflictError(
                        "name", f"A connector named '{params['name']}' already exists"
                    ) from exc
                raise ConnectorConflictError(
                    "id", f"A connector with id '{server_id}' already exists"
                ) from exc
            raise ConnectorStoreUnavailableError(f"connector write failed: {exc}") from exc
        return bool(getattr(result, "rowcount", 1))

    async def delete(self, tenant_id: str, server_id: str) -> bool:
        from sqlalchemy import text

        try:
            async with self._db() as s, s.begin(), self._tx(s, tenant_id):
                result = await s.execute(
                    text("DELETE FROM mcp_servers WHERE tenant_id = :t AND id = :id"),
                    {"t": tenant_id, "id": server_id},
                )
        except Exception as exc:
            raise ConnectorStoreUnavailableError(f"connector delete failed: {exc}") from exc
        return bool(getattr(result, "rowcount", 0))

    async def get_builtins_marker(self, tenant_id: str) -> dict[str, Any] | None:
        from sqlalchemy import text

        try:
            async with self._db() as s, s.begin(), self._tx(s, tenant_id):
                row = (
                    await s.execute(
                        text(
                            "SELECT fingerprint, builtin_ids FROM mcp_builtin_provisioning "
                            "WHERE tenant_id = :t"
                        ),
                        {"t": tenant_id},
                    )
                ).fetchone()
        except Exception as exc:
            raise ConnectorStoreUnavailableError(f"built-in marker read failed: {exc}") from exc
        if row is None:
            return None
        ids = row[1] if isinstance(row[1], list) else json.loads(row[1] or "[]")
        return {"fp": str(row[0]), "ids": [str(i) for i in ids]}

    async def set_builtins_marker(
        self, tenant_id: str, fingerprint: str, ids: list[str], *, only_if_absent: bool = False
    ) -> None:
        from sqlalchemy import text

        conflict = (
            "DO NOTHING"
            if only_if_absent
            else "DO UPDATE SET fingerprint = EXCLUDED.fingerprint, "
            "builtin_ids = EXCLUDED.builtin_ids, updated_at = NOW()"
        )
        try:
            async with self._db() as s, s.begin(), self._tx(s, tenant_id):
                await s.execute(
                    text(
                        "INSERT INTO mcp_builtin_provisioning (tenant_id, fingerprint, "
                        "builtin_ids) VALUES (:t, :fp, CAST(:ids AS jsonb)) "
                        f"ON CONFLICT (tenant_id) {conflict}"
                    ),
                    {"t": tenant_id, "fp": fingerprint[:128], "ids": json.dumps(sorted(ids))},
                )
        except Exception as exc:
            raise ConnectorStoreUnavailableError(f"built-in marker write failed: {exc}") from exc


async def copy_legacy_server(
    rows: PostgresConnectorRows, tenant_id: str, server_id: str, raw: str
) -> str:
    """Copy one legacy Redis config into Postgres without overwriting anything.

    Returns "copied", "present" (Postgres already has the id — Postgres wins) or
    "renamed" (another connector of the tenant holds the display name: the
    legacy one is kept under "<name> (<id>)" rather than dropped).
    """
    config = _as_dict(raw)
    config["server_id"] = server_id
    if await rows.write(tenant_id, server_id, config, mode="insert_missing"):
        return "copied"
    if await rows.get(tenant_id, server_id) is not None:
        return "present"
    original = str(config.get("name") or server_id)
    config["name"] = f"{original} ({server_id})"[:200]
    if await rows.write(tenant_id, server_id, config, mode="insert_missing"):
        return "renamed"
    if await rows.get(tenant_id, server_id) is not None:
        return "present"
    raise ConnectorStoreUnavailableError(
        f"legacy connector {server_id!r} of tenant {tenant_id!r} could not be copied"
    )


class BackfillState:
    """Whether the one-time Redis -> Postgres copy has completed.

    Completion is permanent, so once seen it is remembered; until then it is
    re-checked at most every ``recheck_s`` seconds. While incomplete (or when the
    check itself fails) callers keep the legacy Redis read-repair enabled.
    """

    def __init__(self, db_factory: Callable[[], Any], *, recheck_s: float = 30.0) -> None:
        self._db = db_factory
        self._done = False
        self._checked_at = 0.0
        self._recheck_s = recheck_s

    def mark_done(self) -> None:
        self._done = True

    async def legacy_reads_needed(self) -> bool:
        if self._done:
            return False
        now = time.monotonic()
        if now - self._checked_at < self._recheck_s and self._checked_at:
            return True
        self._checked_at = now
        try:
            self._done = await backfill_completed(self._db)
        except Exception:
            return True
        return not self._done


async def backfill_completed(db_factory: Callable[[], Any], name: str = BACKFILL_NAME) -> bool:
    from sqlalchemy import text

    async with db_factory() as s, s.begin():
        row = (
            await s.execute(
                text("SELECT 1 FROM connector_store_backfills WHERE name = :n"), {"n": name}
            )
        ).fetchone()
    return row is not None


__all__ = [
    "BACKFILL_NAME",
    "CACHE_KEY",
    "LEGACY_BUILTINS_MARKER_KEY",
    "LEGACY_INDEX_KEY",
    "LEGACY_SERVER_KEY",
    "BackfillState",
    "ConnectorConflictError",
    "ConnectorStoreUnavailableError",
    "PostgresConnectorRows",
    "backfill_completed",
    "copy_legacy_server",
    "name_key",
]
