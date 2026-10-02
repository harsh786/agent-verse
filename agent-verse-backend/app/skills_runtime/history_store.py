"""Durable skills-runtime history: executions and archived versions (OPS-04).

Both live in tenant-scoped FORCE-RLS tables (``skill_executions``,
``skill_versions``), so history is shared by every replica, survives a
restart and is isolated per tenant (versions used to be keyed by skill_id
only). Reads are newest-first keyset pages on the
``(tenant_id, skill_id, <ts> DESC, id)`` indexes; ``cursor`` is the opaque
``next_cursor`` of the previous page.

DB failures raise :class:`SkillHistoryUnavailableError` (callers answer 503).
The DB-less dev/test build keeps bounded history in this process.
"""

from __future__ import annotations

import datetime
import json
import uuid
from collections import deque
from typing import Any

from app.db.rls import sqlalchemy_rls_context

MAX_PAGE = 100
_LOCAL_CAP = 1000

# DB-less build only, keyed (tenant_id, skill_id).
_local_executions: dict[tuple[str, str], deque[dict[str, Any]]] = {}
_local_versions: dict[tuple[str, str], deque[dict[str, Any]]] = {}


class SkillHistoryUnavailableError(RuntimeError):
    """Skill history could not be read or written (callers answer 503)."""


class InvalidCursorError(ValueError):
    """The pagination cursor is malformed (callers answer 422)."""


def _now() -> datetime.datetime:
    return datetime.datetime.now(datetime.UTC)


def _encode_cursor(ts: datetime.datetime, row_id: str) -> str:
    return f"{ts.isoformat()}|{row_id}"


def _decode_cursor(cursor: str | None) -> tuple[datetime.datetime, str] | None:
    if not cursor:
        return None
    try:
        ts_raw, row_id = cursor.rsplit("|", 1)
        ts = datetime.datetime.fromisoformat(ts_raw)
    except ValueError as exc:
        raise InvalidCursorError("invalid cursor") from exc
    if ts.tzinfo is None or not row_id:
        raise InvalidCursorError("invalid cursor")
    return ts, row_id


def _page(
    rows: list[dict[str, Any]], limit: int, ts_key: str, id_key: str
) -> tuple[list[dict[str, Any]], str | None]:
    has_more = len(rows) > limit
    rows = rows[:limit]
    nxt = None
    if has_more and rows:
        last = rows[-1]
        nxt = _encode_cursor(datetime.datetime.fromisoformat(last[ts_key]), last[id_key])
    return rows, nxt


def _local_page(
    items: deque[dict[str, Any]] | None,
    limit: int,
    cursor: str | None,
    ts_key: str,
    id_key: str,
) -> tuple[list[dict[str, Any]], str | None]:
    after = _decode_cursor(cursor)
    ordered = sorted(
        items or [], key=lambda r: (r[ts_key], r[id_key]), reverse=True
    )
    if after is not None:
        ts, rid = after
        ordered = [
            r
            for r in ordered
            if (datetime.datetime.fromisoformat(r[ts_key]), r[id_key]) < (ts, rid)
        ]
    return _page(ordered[: limit + 1], limit, ts_key, id_key)


# ── executions ────────────────────────────────────────────────────────────────


async def record_execution(db_factory: Any, execution: dict[str, Any]) -> None:
    """Persist one execution row. Raises unless it committed."""
    row = {
        "execution_id": execution["execution_id"],
        "tenant_id": str(execution["tenant_id"]),
        "skill_id": str(execution["skill_id"]),
        "skill_name": execution.get("skill_name"),
        "goal_id": execution.get("goal_id"),
        "input_preview": str(execution.get("input_preview") or "")[:500],
        "output_preview": str(execution.get("output_preview") or "")[:500],
        "success": bool(execution.get("success")),
        "error": execution.get("error"),
        "duration_ms": execution.get("duration_ms"),
        "model_used": execution.get("model_used"),
        "created_at": _now(),
    }
    if db_factory is None:
        key = (row["tenant_id"], row["skill_id"])
        _local_executions.setdefault(key, deque(maxlen=_LOCAL_CAP)).append(
            {**row, "created_at": row["created_at"].isoformat()}
        )
        return
    from sqlalchemy import text

    try:
        async with (
            db_factory() as session,
            session.begin(),
            sqlalchemy_rls_context(session, row["tenant_id"]),
        ):
            await session.execute(
                text(
                    "INSERT INTO skill_executions (execution_id, tenant_id, skill_id, "
                    "skill_name, goal_id, input_preview, output_preview, success, error, "
                    "duration_ms, model_used, created_at) VALUES (:execution_id, :tenant_id, "
                    ":skill_id, :skill_name, :goal_id, :input_preview, :output_preview, "
                    ":success, :error, :duration_ms, :model_used, :created_at)"
                ),
                row,
            )
    except Exception as exc:
        raise SkillHistoryUnavailableError(str(exc)) from exc


async def list_executions(
    db_factory: Any,
    tenant_id: str,
    skill_id: str,
    *,
    limit: int = 20,
    cursor: str | None = None,
) -> tuple[list[dict[str, Any]], str | None]:
    """A newest-first page of the tenant's executions of *skill_id*."""
    limit = max(1, min(limit, MAX_PAGE))
    if db_factory is None:
        return _local_page(
            _local_executions.get((tenant_id, skill_id)),
            limit,
            cursor,
            "created_at",
            "execution_id",
        )
    after = _decode_cursor(cursor)
    from sqlalchemy import text

    keyset = " AND (created_at, execution_id) < (:cts, :cid)" if after else ""
    params: dict[str, Any] = {"tid": tenant_id, "sid": skill_id, "lim": limit + 1}
    if after:
        params["cts"], params["cid"] = after
    try:
        async with (
            db_factory() as session,
            session.begin(),
            sqlalchemy_rls_context(session, tenant_id),
        ):
            rows = (
                await session.execute(
                    text(
                        "SELECT execution_id, skill_id, skill_name, tenant_id, goal_id, "
                        "input_preview, output_preview, success, error, duration_ms, "
                        "model_used, created_at FROM skill_executions "
                        "WHERE tenant_id = :tid AND skill_id = :sid"
                        f"{keyset} ORDER BY created_at DESC, execution_id DESC LIMIT :lim"
                    ),
                    params,
                )
            ).mappings().all()
    except Exception as exc:
        raise SkillHistoryUnavailableError(str(exc)) from exc
    out = [{**dict(r), "created_at": r["created_at"].isoformat()} for r in rows]
    return _page(out, limit, "created_at", "execution_id")


# ── versions ──────────────────────────────────────────────────────────────────


def _version_row(tenant_id: str, previous: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": uuid.uuid4().hex,
        "tenant_id": tenant_id,
        "skill_id": str(previous["skill_id"]),
        "version": str(previous.get("version") or "1.0.0"),
        "snapshot": previous,
        "archived_at": _now(),
    }


async def archive_version(session: Any, tenant_id: str, previous: dict[str, Any]) -> None:
    """Archive *previous* inside the caller's transaction (``session`` is
    already under the tenant's RLS context). ``session=None`` is the DB-less
    build."""
    row = _version_row(tenant_id, previous)
    if session is None:
        _local_versions.setdefault(
            (tenant_id, row["skill_id"]), deque(maxlen=_LOCAL_CAP)
        ).append(
            {
                **previous,
                "version_id": row["id"],
                "archived_at": row["archived_at"].isoformat(),
            }
        )
        return
    from sqlalchemy import text

    await session.execute(
        text(
            "INSERT INTO skill_versions (id, tenant_id, skill_id, version, snapshot, "
            "archived_at) VALUES (:id, :tenant_id, :skill_id, :version, "
            "CAST(:snapshot AS jsonb), :archived_at)"
        ),
        {**row, "snapshot": json.dumps(previous, default=str)},
    )


async def list_versions(
    db_factory: Any,
    tenant_id: str,
    skill_id: str,
    *,
    limit: int = 20,
    cursor: str | None = None,
) -> tuple[list[dict[str, Any]], str | None]:
    """A newest-first page of the tenant's archived versions of *skill_id*."""
    limit = max(1, min(limit, MAX_PAGE))
    if db_factory is None:
        return _local_page(
            _local_versions.get((tenant_id, skill_id)), limit, cursor, "archived_at", "version_id"
        )
    after = _decode_cursor(cursor)
    from sqlalchemy import text

    keyset = " AND (archived_at, id) < (:cts, :cid)" if after else ""
    params: dict[str, Any] = {"tid": tenant_id, "sid": skill_id, "lim": limit + 1}
    if after:
        params["cts"], params["cid"] = after
    try:
        async with (
            db_factory() as session,
            session.begin(),
            sqlalchemy_rls_context(session, tenant_id),
        ):
            rows = (
                await session.execute(
                    text(
                        "SELECT id, snapshot, archived_at FROM skill_versions "
                        "WHERE tenant_id = :tid AND skill_id = :sid"
                        f"{keyset} ORDER BY archived_at DESC, id DESC LIMIT :lim"
                    ),
                    params,
                )
            ).mappings().all()
    except Exception as exc:
        raise SkillHistoryUnavailableError(str(exc)) from exc
    out = []
    for r in rows:
        snap = r["snapshot"] if isinstance(r["snapshot"], dict) else json.loads(r["snapshot"])
        out.append(
            {**snap, "version_id": r["id"], "archived_at": r["archived_at"].isoformat()}
        )
    return _page(out, limit, "archived_at", "version_id")
