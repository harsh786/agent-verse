"""One-time copy of the legacy Redis connector store into Postgres.

Copies, per tenant and without overwriting anything already in Postgres
(``ON CONFLICT DO NOTHING`` — Postgres wins):

* connector configs   ``mcp:servers:{t}:{id}`` (via the ``mcp:server_ids:{t}`` index)
                      -> ``mcp_servers``
* built-in markers    ``mcp:builtins_provisioned:{t}`` -> ``mcp_builtin_provisioning``

Redis keys are NEVER deleted. The run then verifies that every legacy entry is
present in Postgres; only a run with zero errors and a clean verification
records ``connector_store_backfills`` (which turns off the registry's legacy
read-repair). Run it with ``agentverse connectors-backfill``; the API lifespan
also runs it on startup while it is not yet recorded (one replica at a time,
under a Postgres advisory lock).
"""

from __future__ import annotations

import json
import logging
from collections.abc import Callable
from typing import Any

from app.mcp.connector_store import (
    BACKFILL_NAME,
    PostgresConnectorRows,
    backfill_completed,
    copy_legacy_server,
)

_log = logging.getLogger(__name__)

_INDEX_PREFIX = "mcp:server_ids:"
_SERVER_PREFIX = "mcp:servers:"
_MARKER_PREFIX = "mcp:builtins_provisioned:"
_LOCK_KEY = 0x6D6370_0001  # pg advisory lock id for the backfill


def _text(raw: Any) -> str:
    return raw.decode() if isinstance(raw, bytes) else str(raw)


async def _scan(redis: Any, pattern: str) -> list[str]:
    return [_text(k) async for k in redis.scan_iter(match=pattern, count=500)]


async def backfill_connectors_from_redis(
    redis: Any, db_factory: Callable[[], Any], *, record: bool = True
) -> dict[str, Any]:
    """Copy the legacy Redis connector store into Postgres; idempotent."""
    rows = PostgresConnectorRows(db_factory)
    report: dict[str, Any] = {
        "status": "failed",
        "tenants": 0,
        "servers": {"copied": 0, "present": 0, "renamed": 0},
        "builtin_markers": 0,
        "missing_after_copy": 0,
        "errors": [],
        "redis_keys_deleted": 0,
    }
    tenants: set[str] = set()

    # ── connector configs ────────────────────────────────────────────────
    try:
        index_keys = await _scan(redis, f"{_INDEX_PREFIX}*")
    except Exception as exc:
        report["errors"].append(f"redis scan: {type(exc).__name__}: {exc}"[:300])
        return report
    expected_servers: list[tuple[str, str]] = []
    for index_key in index_keys:
        tenant = index_key[len(_INDEX_PREFIX) :]
        tenants.add(tenant)
        try:
            ids = sorted(_text(i) for i in await redis.smembers(index_key))
            raws = (
                await redis.mget([f"{_SERVER_PREFIX}{tenant}:{sid}" for sid in ids]) if ids else []
            )
        except Exception as exc:
            report["errors"].append(f"redis read {tenant}: {type(exc).__name__}"[:300])
            continue
        for sid, raw in zip(ids, raws, strict=True):
            if raw is None:
                continue  # dangling index entry: nothing to copy
            expected_servers.append((tenant, sid))
            try:
                outcome = await copy_legacy_server(rows, tenant, sid, _text(raw))
                report["servers"][outcome] += 1
            except Exception as exc:
                report["errors"].append(f"server {tenant}/{sid}: {type(exc).__name__}: {exc}"[:300])

    # ── built-in provisioning markers ─────────────────────────────────────
    try:
        marker_keys = await _scan(redis, f"{_MARKER_PREFIX}*")
    except Exception as exc:
        report["errors"].append(f"redis scan markers: {type(exc).__name__}"[:300])
        marker_keys = []
    for marker_key in marker_keys:
        tenant = marker_key[len(_MARKER_PREFIX) :]
        try:
            raw_marker = await redis.get(marker_key)
            if raw_marker is None:
                continue
            try:
                state = json.loads(_text(raw_marker))
            except ValueError:
                state = None
            if isinstance(state, dict):
                fp, ids = str(state.get("fp") or ""), [str(i) for i in state.get("ids") or []]
            else:
                fp, ids = "legacy", []  # pre-fingerprint marker: refresh, insert nothing
            await rows.set_builtins_marker(tenant, fp, ids, only_if_absent=True)
            report["builtin_markers"] += 1
        except Exception as exc:
            report["errors"].append(f"marker {tenant}: {type(exc).__name__}: {exc}"[:300])

    # ── verify: every legacy entry is now in Postgres ─────────────────────
    missing = 0
    for tenant, sid in expected_servers:
        try:
            if await rows.get(tenant, sid) is None:
                missing += 1
        except Exception:
            missing += 1
    report["missing_after_copy"] = missing
    report["tenants"] = len(tenants)

    if report["errors"] or missing:
        _log.error(
            "connector_backfill_incomplete errors=%d missing=%d", len(report["errors"]), missing
        )
        return report
    report["status"] = "complete"
    if record:
        await _record_completion(db_factory, report)
    _log.info(
        "connector_backfill_complete tenants=%d servers=%s",
        report["tenants"],
        report["servers"],
    )
    return report


async def _record_completion(db_factory: Callable[[], Any], report: dict[str, Any]) -> None:
    from sqlalchemy import text

    async with db_factory() as s, s.begin():
        await s.execute(
            text(
                "INSERT INTO connector_store_backfills (name, report) "
                "VALUES (:n, CAST(:r AS jsonb)) ON CONFLICT (name) DO UPDATE SET "
                "report = EXCLUDED.report, completed_at = NOW()"
            ),
            {"n": BACKFILL_NAME, "r": json.dumps(report)},
        )


async def ensure_connector_backfill(redis: Any, db_factory: Callable[[], Any]) -> dict[str, Any]:
    """Startup hook: run the copy once, on one replica at a time.

    Skips when it is already recorded. Otherwise takes a transaction-scoped
    Postgres advisory lock (``pg_try_advisory_xact_lock``) held on a dedicated
    session for the whole run; a replica that cannot take it skips (the other
    one is copying, and the registry's read-repair covers the window).
    """
    from sqlalchemy import text

    if await backfill_completed(db_factory):
        return {"status": "already_complete"}
    async with db_factory() as lock_session, lock_session.begin():
        got = (
            await lock_session.execute(
                text("SELECT pg_try_advisory_xact_lock(:k)"), {"k": _LOCK_KEY}
            )
        ).scalar()
        if not got:
            return {"status": "running_elsewhere"}
        if await backfill_completed(db_factory):
            return {"status": "already_complete"}
        return await backfill_connectors_from_redis(redis, db_factory)


__all__ = ["backfill_connectors_from_redis", "ensure_connector_backfill"]
