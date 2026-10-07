"""Durable connector secret store (SECRET-01).

Encrypted connector credentials lived only in Redis
(``mcp:connector_secrets:{tenant}:{server}:{key}``): a FLUSHALL or an eviction
lost every tenant's credentials with no recovery.

Postgres ``mcp_credentials`` (RLS, PK tenant_id+server_id+secret_key; migration
a7c4e2f9d1b3) is now the source of truth. Values are sealed with the
tenant's envelope key when it has one (``tv1:``; TENANT-ENVELOPE-ALL helpers
``ensure_tenant_vault`` / ``seal_for_tenant`` / ``open_for_tenant``, with the
same lazy compare-and-swap re-wrap of platform / replaced-key values), else
the platform vault. Redis caches only the
CIPHERTEXT for a short TTL (``mcp:secretcache:v1:...``); every write and delete
invalidates it (shared Redis, so on every replica).

Until the one-time Redis -> Postgres copy is recorded as complete, a Postgres
miss falls back to the legacy Redis key and copies it (read-repair). The copy
never deletes legacy keys; a store/delete of a secret removes the superseded
legacy key for that secret only (so it cannot be resurrected by a re-run copy).
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from typing import Any

from app.db.rls import sqlalchemy_rls_context
from app.providers.tenant_vault import TenantVaultReadError
from app.providers.vault import (
    ConnectorSecretUnavailableError,
    ConnectorSecretUndecryptableError,
    is_connector_secret_ref,
)
from app.providers.vault_canary import explain_undecryptable_secret

_log = logging.getLogger(__name__)

LEGACY_PREFIX = "mcp:connector_secrets"
CACHE_PREFIX = "mcp:secretcache:v1"


def parse_secret_ref(ref: str) -> tuple[str, str]:
    """``(server_id, key)`` of a ``vault://connectors/..`` / ``secret://connector/..`` ref."""
    if ref.startswith("secret://connector/"):
        remainder = ref[len("secret://connector/") :]
    elif is_connector_secret_ref(ref):
        remainder = ref[len("vault://connectors/") :]
    else:
        raise ValueError(f"Unrecognized secret ref format: {ref!r}")
    # Same split as the legacy RedisConnectorSecretStore: "<server_id>/<key>".
    server_id, separator, key = remainder.partition("/")
    if not server_id or not separator or not key:
        raise ValueError(f"Invalid secret ref format: {ref!r}")
    return server_id, key


def _tenant_id(tenant_ctx: Any) -> str:
    return str(getattr(tenant_ctx, "tenant_id", "") or "global")


def _text(raw: Any) -> str | None:
    if raw is None:
        return None
    return raw.decode() if isinstance(raw, bytes) else str(raw)


class DurableConnectorSecretStore:
    """Postgres-backed, tenant-scoped connector secret store (Redis = cache)."""

    production_safe = True

    def __init__(
        self,
        *,
        db_factory: Callable[[], Any],
        redis: Any = None,
        cache_ttl_s: int = 300,
        legacy_prefix: str = LEGACY_PREFIX,
    ) -> None:
        from app.mcp.connector_store import BackfillState

        self._db = db_factory
        self._redis = redis
        self._ttl = cache_ttl_s
        self._legacy_prefix = legacy_prefix
        self._backfill = BackfillState(db_factory)

    # ── keys ────────────────────────────────────────────────────────────────

    def _legacy_key(self, tenant_id: str, server_id: str, key: str) -> str:
        return f"{self._legacy_prefix}:{tenant_id}:{server_id}:{key}"

    def _cache_key(self, tenant_id: str, server_id: str, key: str) -> str:
        return f"{CACHE_PREFIX}:{tenant_id}:{server_id}:{key}"

    # ── Postgres ────────────────────────────────────────────────────────────

    async def _pg_get(self, tenant_id: str, server_id: str, key: str) -> str | None:
        from sqlalchemy import text

        try:
            async with (
                self._db() as s,
                s.begin(),
                sqlalchemy_rls_context(s, tenant_id),
            ):
                row = (
                    await s.execute(
                        text(
                            "SELECT encrypted_value FROM mcp_credentials WHERE tenant_id = :t "
                            "AND server_id = :s AND secret_key = :k"
                        ),
                        {"t": tenant_id, "s": server_id, "k": key},
                    )
                ).fetchone()
        except Exception as exc:
            raise ConnectorSecretUnavailableError(
                f"connector secret store unavailable: {type(exc).__name__}"
            ) from exc
        return str(row[0]) if row is not None else None

    async def _pg_put(
        self, tenant_id: str, server_id: str, key: str, ciphertext: str, *, only_if_absent: bool
    ) -> None:
        from sqlalchemy import text

        conflict = (
            "DO NOTHING"
            if only_if_absent
            else "DO UPDATE SET encrypted_value = EXCLUDED.encrypted_value, updated_at = NOW()"
        )
        async with (
            self._db() as s,
            s.begin(),
            sqlalchemy_rls_context(s, tenant_id),
        ):
            await s.execute(
                text(
                    "INSERT INTO mcp_credentials (tenant_id, server_id, secret_key, "
                    "encrypted_value) VALUES (:t, :s, :k, :v) "
                    f"ON CONFLICT (tenant_id, server_id, secret_key) {conflict}"
                ),
                {"t": tenant_id, "s": server_id, "k": key, "v": ciphertext},
            )

    # ── Redis (cache + legacy) — never fails a request ──────────────────────

    async def _redis_call(self, op: str, *args: Any, **kwargs: Any) -> Any:
        if self._redis is None:
            return None
        try:
            return await getattr(self._redis, op)(*args, **kwargs)
        except Exception as exc:
            _log.warning("connector_secret_redis_%s_failed error=%s", op, exc)
            return None

    # ── public API (matches RedisConnectorSecretStore) ──────────────────────

    async def _tenant_vault(self, tenant_id: str) -> Any:
        """The tenant's envelope key (TENANT-ENVELOPE-ALL helpers; raises on read errors)."""
        from app.providers.tenant_vault import ensure_tenant_vault

        return await ensure_tenant_vault(self._db, tenant_id)

    async def store(self, ref: str, value: str, *, tenant_ctx: Any = None) -> None:
        from app.providers.tenant_vault import seal_for_tenant

        tenant_id = _tenant_id(tenant_ctx)
        server_id, key = parse_secret_ref(ref)
        try:
            ciphertext = seal_for_tenant(await self._tenant_vault(tenant_id), value)
            await self._pg_put(tenant_id, server_id, key, ciphertext, only_if_absent=False)
        except Exception as exc:
            raise ConnectorSecretUnavailableError(
                f"connector secret could not be stored: {type(exc).__name__}"
            ) from exc
        await self._redis_call(
            "delete",
            self._cache_key(tenant_id, server_id, key),
            self._legacy_key(tenant_id, server_id, key),
        )

    async def resolve(self, ref: str, *, tenant_ctx: Any = None) -> str | None:
        from app.providers.tenant_vault import (
            is_tenant_encrypted,
            needs_rewrap,
            open_for_tenant,
            seal_for_tenant,
        )

        tenant_id = _tenant_id(tenant_ctx)
        server_id, key = parse_secret_ref(ref)
        cache_key = self._cache_key(tenant_id, server_id, key)
        ciphertext = _text(await self._redis_call("get", cache_key))
        if ciphertext is None:
            ciphertext = await self._pg_get(tenant_id, server_id, key)
            if ciphertext is None and await self._legacy_reads():
                legacy = _text(
                    await self._redis_call("get", self._legacy_key(tenant_id, server_id, key))
                )
                if legacy is not None:
                    await self._pg_put(tenant_id, server_id, key, legacy, only_if_absent=True)
                    ciphertext = await self._pg_get(tenant_id, server_id, key)
            if ciphertext is None:
                return None
            await self._redis_call("set", cache_key, ciphertext, ex=self._ttl)
        try:
            if is_tenant_encrypted(ciphertext):
                # Needs the tenant key: missing / unreadable raises (fail closed).
                tenant_vault = await self._tenant_vault(tenant_id)
                plaintext = open_for_tenant(tenant_vault, ciphertext)
            else:
                plaintext = open_for_tenant(None, ciphertext)
                try:  # only needed to re-wrap: best effort
                    tenant_vault = await self._tenant_vault(tenant_id)
                except Exception as exc:
                    _log.warning("connector_secret_rewrap_skipped: %s", type(exc).__name__)
                    tenant_vault = None
        except TenantVaultReadError as exc:
            raise ConnectorSecretUnavailableError(
                "connector secret store unavailable: the tenant vault key could not be read"
            ) from exc
        except Exception as exc:
            # The value IS stored but does not open here. This used to surface as
            # "re-enter the connector's credentials" even when the cause was this
            # process running with another VAULT_MASTER_KEY than the API.
            _log.error(
                "connector_secret_undecryptable tenant=%s server=%s key=%s error=%s",
                tenant_id,
                server_id,
                key,
                type(exc).__name__,
            )
            raise ConnectorSecretUndecryptableError(
                await explain_undecryptable_secret(self._db, exc)
            ) from exc
        if needs_rewrap(tenant_vault, ciphertext):
            await self._rewrap(
                tenant_id, server_id, key, ciphertext, seal_for_tenant(tenant_vault, plaintext)
            )
        return plaintext

    async def _rewrap(self, tenant_id: str, server_id: str, key: str, old: str, new: str) -> None:
        """Lazy re-seal with the tenant's current key: compare-and-swap, never fatal."""
        from sqlalchemy import text

        try:
            async with (
                self._db() as s,
                s.begin(),
                sqlalchemy_rls_context(s, tenant_id),
            ):
                await s.execute(
                    text(
                        "UPDATE mcp_credentials SET encrypted_value = :new, updated_at = NOW() "
                        "WHERE tenant_id = :t AND server_id = :s AND secret_key = :k "
                        "AND encrypted_value = :old"
                    ),
                    {"new": new, "old": old, "t": tenant_id, "s": server_id, "k": key},
                )
        except Exception as exc:
            _log.warning("connector_secret_rewrap_failed: %s", type(exc).__name__)
            return
        await self._redis_call("delete", self._cache_key(tenant_id, server_id, key))

    async def delete_server(self, server_id: str, *, tenant_ctx: Any = None) -> int:
        """Erase every secret of one connector (Postgres rows, cache, legacy keys)."""
        from sqlalchemy import text

        tenant_id = _tenant_id(tenant_ctx)
        try:
            async with (
                self._db() as s,
                s.begin(),
                sqlalchemy_rls_context(s, tenant_id),
            ):
                rows = (
                    await s.execute(
                        text(
                            "DELETE FROM mcp_credentials WHERE tenant_id = :t AND server_id = :s "
                            "RETURNING secret_key"
                        ),
                        {"t": tenant_id, "s": server_id},
                    )
                ).fetchall()
        except Exception as exc:
            raise ConnectorSecretUnavailableError(
                f"connector secrets could not be deleted: {type(exc).__name__}"
            ) from exc
        keys = {str(r[0]) for r in rows}
        if self._redis is not None:
            # Legacy keys of this server (also ones never copied to Postgres).
            pattern = f"{self._legacy_prefix}:{tenant_id}:{server_id}:*"
            try:
                async for raw_key in self._redis.scan_iter(match=pattern, count=200):
                    keys.add((_text(raw_key) or "").rsplit(":", 1)[-1])
            except Exception as exc:
                _log.warning("connector_secret_legacy_scan_failed error=%s", exc)
            doomed = [self._cache_key(tenant_id, server_id, k) for k in keys] + [
                self._legacy_key(tenant_id, server_id, k) for k in keys
            ]
            if doomed:
                await self._redis_call("delete", *doomed)
        return len(rows)

    async def _legacy_reads(self) -> bool:
        return self._redis is not None and bool(await self._backfill.legacy_reads_needed())


__all__ = ["CACHE_PREFIX", "LEGACY_PREFIX", "DurableConnectorSecretStore", "parse_secret_ref"]
