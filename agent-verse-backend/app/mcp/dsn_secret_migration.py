"""Seal connection strings that older connectors stored in clear (MDB-01 backfill).

Connectors registered before MDB-01 hold ``mongodb://user:password@...`` in the
``mcp_servers`` row (``url`` column and ``config`` JSON) and in the legacy Redis
copy (``mcp:servers:{tenant}:{id}``). :func:`migrate_plaintext_dsn_connectors`
moves each one into the vault-encrypted connector secret store with
:func:`app.mcp.dsn_secrets.seal_connector_dsns` — exactly what a fresh
registration stores — and rewrites the row.

Idempotent and safe to run concurrently / repeatedly:

* the secret is stored first (an upsert of the same reference and value), then
  the row is rewritten with a compare-and-swap on the old ``config``: a
  connector edited meanwhile is left alone (the edit already sealed it);
* a sealed row no longer matches the candidate filter, so a re-run touches
  nothing;
* the candidate scan is keyset-paginated over the primary key
  ``(tenant_id, id)`` (bounded memory at any number of connectors). It needs
  the cross-tenant (BYPASSRLS maintenance) session factory; every write runs on
  the tenant's RLS-scoped session with an explicit ``tenant_id`` predicate.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Callable
from typing import Any

from app.db.rls import sqlalchemy_rls_context
from app.mcp.dsn_secrets import config_has_plaintext_dsn, seal_connector_dsns

_log = logging.getLogger(__name__)

_LEGACY_SERVER_PREFIX = "mcp:servers:"
_CACHE_KEY = "mcp:cfgcache:v1:{tenant}:{server}"
_LOCK_KEY = 0x6D6370_0002  # pg advisory lock id (connector_backfill uses ..._0001)
# A cheap pre-filter; the exact decision is config_has_plaintext_dsn().
_CANDIDATE_SQL = (
    "SELECT tenant_id, id, config FROM mcp_servers "
    "WHERE (tenant_id, id) > (:t, :id) "
    "AND (url ~* '^(mongodb|mongodb\\+srv|postgres|postgresql|mysql|rediss?)://' "
    "OR config::text ~* '\"(mongodb|mongodb\\+srv|postgres|postgresql|mysql|rediss?)://') "
    "ORDER BY tenant_id, id LIMIT :n"
)


class _Tenant:
    def __init__(self, tenant_id: str) -> None:
        self.tenant_id = tenant_id


def _text(raw: Any) -> str:
    return raw.decode() if isinstance(raw, bytes) else str(raw)


async def _seal(tenant_id: str, config: dict[str, Any], secret_store: Any) -> dict[str, Any] | None:
    """Store the secrets of one config; the sealed config (None if nothing to do)."""
    from app.mcp.registry import MCPServerConfig
    from app.providers.vault import store_connector_secret_for_tenant

    if not config_has_plaintext_dsn(config):
        return None
    pending: dict[str, str] = {}
    sealed = seal_connector_dsns(MCPServerConfig.model_validate(config), pending)
    for ref, value in pending.items():
        await store_connector_secret_for_tenant(
            ref, value, store=secret_store, tenant_ctx=_Tenant(tenant_id)
        )
    return sealed.model_dump(mode="json")


async def _migrate_redis_legacy(redis: Any, secret_store: Any, report: dict[str, Any]) -> None:
    async for raw_key in redis.scan_iter(match=f"{_LEGACY_SERVER_PREFIX}*", count=500):
        key = _text(raw_key)
        tenant_id, _, server_id = key[len(_LEGACY_SERVER_PREFIX) :].partition(":")
        if not tenant_id or not server_id:
            continue
        try:
            raw = await redis.get(key)
            if raw is None:
                continue
            config = json.loads(_text(raw))
            if not isinstance(config, dict):
                continue
            config.setdefault("server_id", server_id)
            sealed = await _seal(tenant_id, config, secret_store)
            if sealed is None:
                continue
            await redis.set(key, json.dumps(sealed))
            report["redis_sealed"] += 1
        except Exception as exc:
            report["errors"].append(f"redis {tenant_id}: {type(exc).__name__}"[:200])


async def _migrate_row(
    db_factory: Callable[[], Any],
    redis: Any,
    secret_store: Any,
    tenant_id: str,
    server_id: str,
    config: dict[str, Any],
    report: dict[str, Any],
) -> None:
    from sqlalchemy import text

    stored = config
    config = {**config, "server_id": config.get("server_id") or server_id}
    sealed = await _seal(tenant_id, config, secret_store)
    if sealed is None:
        return
    sealed["server_id"] = server_id
    async with db_factory() as s, s.begin(), sqlalchemy_rls_context(s, tenant_id):
        result = await s.execute(
            text(
                "UPDATE mcp_servers SET url = :url, config = CAST(:new AS jsonb), "
                "updated_at = NOW() WHERE tenant_id = :t AND id = :id "
                "AND config = CAST(:old AS jsonb)"
            ),
            {
                "url": str(sealed.get("url") or ""),
                "new": json.dumps(sealed),
                "old": json.dumps(stored),
                "t": tenant_id,
                "id": server_id,
            },
        )
    if int(getattr(result, "rowcount", 0) or 0) == 0:
        report["skipped_concurrent_edit"] += 1
        return
    report["postgres_sealed"] += 1
    if redis is not None:
        try:
            await redis.delete(_CACHE_KEY.format(tenant=tenant_id, server=server_id))
        except Exception as exc:  # bounded by the cache TTL
            _log.warning("dsn_migration_cache_invalidate_failed error=%s", exc)


async def migrate_plaintext_dsn_connectors(
    *,
    db_factory: Callable[[], Any] | None,
    scan_db_factory: Callable[[], Any] | None,
    secret_store: Any,
    redis: Any = None,
    batch_size: int = 200,
) -> dict[str, Any]:
    """Seal every plaintext connection string (legacy Redis copies, then Postgres)."""
    from sqlalchemy import text

    report: dict[str, Any] = {
        "redis_sealed": 0,
        "postgres_sealed": 0,
        "skipped_concurrent_edit": 0,
        "errors": [],
    }
    if secret_store is None:
        report["errors"].append("no connector secret store: nothing sealed")
        return report
    if redis is not None:
        try:
            await _migrate_redis_legacy(redis, secret_store, report)
        except Exception as exc:
            report["errors"].append(f"redis scan: {type(exc).__name__}"[:200])
    if db_factory is None or scan_db_factory is None:
        return report
    after = ("", "")
    while True:
        async with scan_db_factory() as s, s.begin():
            rows = (
                await s.execute(
                    text(_CANDIDATE_SQL), {"t": after[0], "id": after[1], "n": int(batch_size)}
                )
            ).fetchall()
        for tenant_id, server_id, raw in rows:
            config = raw if isinstance(raw, dict) else json.loads(raw)
            try:
                await _migrate_row(
                    db_factory, redis, secret_store, str(tenant_id), str(server_id), config, report
                )
            except Exception as exc:
                report["errors"].append(f"pg {tenant_id}/{server_id}: {type(exc).__name__}"[:200])
        if len(rows) < batch_size:
            break
        after = (str(rows[-1][0]), str(rows[-1][1]))
    if report["errors"]:
        _log.warning("dsn_secret_migration_errors count=%s", len(report["errors"]))
    return report


async def ensure_dsn_secret_migration(
    *,
    db_factory: Callable[[], Any],
    scan_db_factory: Callable[[], Any],
    secret_store: Any,
    redis: Any = None,
) -> dict[str, Any]:
    """Startup hook: one replica at a time (Postgres advisory lock), every boot.

    Cheap once done: the candidate filter matches no sealed row.
    """
    from sqlalchemy import text

    async with scan_db_factory() as lock_session, lock_session.begin():
        got = (
            await lock_session.execute(
                text("SELECT pg_try_advisory_xact_lock(:k)"), {"k": _LOCK_KEY}
            )
        ).scalar()
        if not got:
            return {"status": "running_elsewhere"}
        report = await migrate_plaintext_dsn_connectors(
            db_factory=db_factory,
            scan_db_factory=scan_db_factory,
            secret_store=secret_store,
            redis=redis,
        )
    report["status"] = "failed" if report["errors"] else "complete"
    return report


__all__ = ["ensure_dsn_secret_migration", "migrate_plaintext_dsn_connectors"]
