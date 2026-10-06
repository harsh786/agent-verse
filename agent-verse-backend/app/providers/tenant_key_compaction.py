"""Drop a tenant's replaced envelope keys once nothing is sealed with them
(``agentverse tenant-key-compact``, TENANT-KEY-COMPACT).

Replacing a tenant vault key keeps the previous keys in ``tenant_vault_keys``
(decrypt only) so every ``tv1:`` value stays readable; reads re-wrap values to
the current key lazily. This command finishes the job for each tenant that
still has previous keys:

1. re-seals, under the tenant's RLS context and in batches, every ``tv1:`` value
   that does not open with the current key — tenant LLM key, OAuth tokens,
   trigger webhook secrets, ingestion source credentials, durable connector
   secrets, workflow webhook HMAC secrets (Postgres) and connector
   secrets, OAuth copies in connector configs and the LLM-config cache (Redis) —
   each write a compare-and-swap on the value it read;
2. only when that pass leaves nothing under a previous key and nothing
   unreadable, rewrites the tenant's keyring to the current key alone
   (compare-and-swap on the wrapped value it read).

Never drops a key that something may still need: a value that opens with no key
(unreadable), a lost compare-and-swap, or a key replaced less than
``min_age_seconds`` ago (a replica may still cache the old keyring and seal with
its first key) keeps the previous keys, and the tenant is reported. ``dry_run``
counts what would change and writes nothing. Idempotent: a re-run of a
compacted tenant finds nothing to do.
"""

from __future__ import annotations

import base64
import json
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from app.providers.tenant_vault import (
    TENANT_CIPHER_PREFIX,
    TenantVaultError,
    _unwrap_keys,
    _vault_from_keys,
    invalidate_tenant_vault,
)
from app.providers.vault import CredentialVault

_SOURCE_PREFIX = "enc:v1:"

# (store name, table, primary key, columns, connection_config JSON?, UUID tenant_id?)
_PG_STORES: tuple[tuple[str, str, str, tuple[str, ...], bool, bool], ...] = (
    ("tenant_llm_configs", "tenant_llm_configs", "tenant_id", ("encrypted_key",), False, False),
    ("oauth_tokens", "oauth_tokens", "id", ("access_token", "refresh_token"), False, False),
    (
        "trigger_secrets",
        "schedules",
        "id",
        ("webhook_signature_secret_enc", "webhook_signature_secret_prev_enc"),
        False,
        False,
    ),
    ("source_credentials", "source_configs", "id", ("connection_config",), True, False),
    # Messaging-gateway binding secrets (DEF-3): channel_config.*_enc.
    ("channel_binding_secrets", "channel_tenant_mappings", "id", ("channel_config",), True, False),
    # Durable connector secrets (SECRET-01); composite key -> keyset on the
    # "<server_id>\x1f<secret_key>" expression (a handful of rows per tenant).
    (
        "connector_secrets_pg",
        "mcp_credentials",
        "(server_id || chr(31) || secret_key)",
        ("encrypted_value",),
        False,
        False,
    ),
    # Workflow webhook HMAC secrets (B2-OPEN-1): enc:v1:tv1: values in the
    # definition JSON — builder row, run-engine mirror, version snapshots.
    ("workflow_webhook_secrets", "workflows", "id", ("definition",), True, False),
    (
        "workflow_definition_secrets",
        "workflow_definitions",
        "id::text",
        ("definition_json",),
        True,
        True,
    ),
    (
        "workflow_version_secrets",
        "workflow_definition_versions",
        "id::text",
        ("definition_json",),
        True,
        True,
    ),
)

_REDIS_CAS = (
    "if redis.call('GET', KEYS[1]) == ARGV[1] then "
    "return redis.call('SET', KEYS[1], ARGV[2]) else return nil end"
)


@dataclass
class TenantCompaction:
    tenant_id: str
    previous_keys: int = 0
    scanned: int = 0
    current: int = 0
    resealed: int = 0
    unreadable: int = 0
    lost_races: int = 0
    keys_dropped: int = 0
    status: str = "pending"
    errors: list[str] = field(default_factory=list)


class _Sealer:
    """Opens a tenant ``tv1:`` value with the keyring; re-seals it with the current key."""

    def __init__(self, keys: list[bytes], report: TenantCompaction) -> None:
        self.primary = CredentialVault.from_byok(keys[0])
        self.ring = _vault_from_keys(keys)
        self.report = report

    def reseal(self, value: Any) -> str | None:
        """New ``tv1:`` value, or None when the value needs no change."""
        if not isinstance(value, str) or not value.startswith(TENANT_CIPHER_PREFIX):
            return None
        body = value[len(TENANT_CIPHER_PREFIX) :]
        self.report.scanned += 1
        try:
            self.primary.decrypt(body)
            self.report.current += 1
            return None
        except Exception:
            pass
        try:
            plain = self.ring.decrypt(body)
        except Exception:
            self.report.unreadable += 1
            return None
        self.report.resealed += 1
        return TENANT_CIPHER_PREFIX + self.primary.encrypt(plain)

    def reseal_source(self, config: Any) -> Any:
        """A JSON config with its ``enc:v1:tv1:`` secrets (source credentials,
        workflow webhook secrets) and ``*_enc`` ``tv1:`` values (gateway binding
        secrets) re-sealed (None = unchanged)."""
        if isinstance(config, list):  # e.g. a workflow definition's ``triggers``
            items = [self.reseal_source(item) for item in config]
            if all(item is None for item in items):
                return None
            return [o if n is None else n for o, n in zip(config, items, strict=True)]
        if not isinstance(config, dict):
            return None
        changed, out = False, {}
        for key, value in config.items():
            new: Any = None
            if isinstance(value, str) and value.startswith(_SOURCE_PREFIX):
                inner = self.reseal(value[len(_SOURCE_PREFIX) :])
                new = None if inner is None else _SOURCE_PREFIX + inner
            elif isinstance(value, str) and str(key).endswith("_enc"):
                new = self.reseal(value)
            elif isinstance(value, dict | list):
                new = self.reseal_source(value)
            out[key] = value if new is None else new
            changed = changed or new is not None
        return out if changed else None


async def _keyring_row(tenant_db: Any, tenant_id: str) -> tuple[str, float] | None:
    from sqlalchemy import text

    from app.db.rls import sqlalchemy_rls_context

    async with tenant_db() as s, s.begin(), sqlalchemy_rls_context(s, tenant_id):
        row = (
            await s.execute(
                text(
                    # Age by the database clock (the writers' NOW()), not this host's.
                    "SELECT wrapped_key, EXTRACT(EPOCH FROM (NOW() - updated_at)) "
                    "FROM tenant_vault_keys WHERE tenant_id = :t"
                ),
                {"t": tenant_id},
            )
        ).fetchone()
    return (str(row[0]), float(row[1] or 0.0)) if row is not None else None


async def _pg_pass(
    tenant_db: Any, tenant_id: str, sealer: _Sealer, dry_run: bool, batch_size: int
) -> None:
    from sqlalchemy import text

    from app.db.rls import sqlalchemy_rls_context

    for _name, table, pk, columns, source_json, uuid_tenant in _PG_STORES:
        tenant_param = tenant_id
        if uuid_tenant:
            import uuid as _uuid

            try:
                tenant_param = str(_uuid.UUID(str(tenant_id)))
            except (ValueError, TypeError, AttributeError):
                continue  # a non-UUID tenant has no rows in a UUID-keyed table
        tenant_match = "tenant_id = CAST(:t AS uuid)" if uuid_tenant else "tenant_id = :t"
        after = ""
        while True:
            async with tenant_db() as s, s.begin(), sqlalchemy_rls_context(s, tenant_id):
                rows = (
                    await s.execute(
                        text(
                            f"SELECT {pk}, {', '.join(columns)} FROM {table} "
                            f"WHERE {tenant_match} AND {pk} > :after ORDER BY {pk} LIMIT :n"
                        ),
                        {"t": tenant_param, "after": after, "n": batch_size},
                    )
                ).fetchall()
                for row in rows:
                    updates: dict[str, Any] = {}
                    olds: dict[str, Any] = {}
                    for col, value in zip(columns, row[1:], strict=True):
                        if source_json:
                            data = json.loads(value) if isinstance(value, str) else value
                            new = sealer.reseal_source(data)
                            if new is not None:
                                updates[col], olds[col] = json.dumps(new), json.dumps(data)
                        else:
                            new = sealer.reseal(value)
                            if new is not None:
                                updates[col], olds[col] = new, value
                    if not updates or dry_run:
                        continue
                    sets = ", ".join(
                        f"{c} = CAST(:n_{c} AS jsonb)" if source_json else f"{c} = :n_{c}"
                        for c in updates
                    )
                    guards = " AND ".join(
                        f"{c} = CAST(:o_{c} AS jsonb)" if source_json else f"{c} = :o_{c}"
                        for c in olds
                    )
                    params = {f"n_{c}": v for c, v in updates.items()}
                    params.update({f"o_{c}": v for c, v in olds.items()})
                    result = await s.execute(
                        text(
                            f"UPDATE {table} SET {sets} "
                            f"WHERE {tenant_match} AND {pk} = :pk AND {guards}"
                        ),
                        {**params, "t": tenant_param, "pk": row[0]},
                    )
                    if not getattr(result, "rowcount", 0):
                        sealer.report.lost_races += 1
            if len(rows) < batch_size:
                break
            after = str(rows[-1][0])


def _glob(value: str) -> str:
    return "".join(f"[{c}]" if c in "*?[]\\" else c for c in value)


async def _redis_pass(redis: Any, tenant_id: str, sealer: _Sealer, dry_run: bool) -> None:
    t = _glob(tenant_id)

    async def _cas(key: Any, old: str, new: str) -> None:
        if dry_run:
            return
        if await redis.eval(_REDIS_CAS, 1, key, old, new) is None:
            sealer.report.lost_races += 1

    if not dry_run:
        # Short-TTL read caches of the durable connector stores may hold values
        # sealed with a key about to be dropped: drop them (they refill from
        # Postgres, re-sealed above).
        for cache in (f"mcp:secretcache:v1:{t}:*", f"mcp:cfgcache:v1:{t}:*"):
            async for key in redis.scan_iter(match=cache, count=200):
                await redis.delete(key)

    async for key in redis.scan_iter(match=f"mcp:connector_secrets:{t}:*", count=200):
        raw = await redis.get(key)
        value = raw.decode() if isinstance(raw, bytes) else raw
        new = sealer.reseal(value)
        if new is not None:
            await _cas(key, value, new)

    for match, fields, nested in (
        (f"mcp:servers:{t}:*", ("_encrypted_access_token", "_encrypted_refresh_token"),
         "auth_config"),
        (f"llm_config:{t}", ("encrypted_key",), ""),
    ):
        async for key in redis.scan_iter(match=match, count=200):
            raw = await redis.get(key)
            if not raw:
                continue
            text_value = raw.decode() if isinstance(raw, bytes) else str(raw)
            try:
                doc = json.loads(text_value)
            except ValueError:
                continue
            target = doc.get(nested) if nested else doc
            if not isinstance(target, dict):
                continue
            changed = False
            for fld in fields:
                new = sealer.reseal(target.get(fld))
                if new is not None:
                    target[fld], changed = new, True
            if changed:
                await _cas(key, text_value, json.dumps(doc))


async def _drop_previous(tenant_db: Any, tenant_id: str, wrapped: str, primary: bytes) -> bool:
    from sqlalchemy import text

    from app.db.rls import sqlalchemy_rls_context
    from app.providers import vault as vault_mod

    new_wrapped = vault_mod.get_vault().encrypt(base64.b64encode(primary).decode())
    async with tenant_db() as s, s.begin(), sqlalchemy_rls_context(s, tenant_id):
        result = await s.execute(
            text(
                "UPDATE tenant_vault_keys SET wrapped_key = :new, updated_at = NOW() "
                "WHERE tenant_id = :t AND wrapped_key = :old"
            ),
            {"new": new_wrapped, "t": tenant_id, "old": wrapped},
        )
    invalidate_tenant_vault(tenant_id)
    return bool(getattr(result, "rowcount", 0))


async def _tenants_with_keys(system_db: Any) -> list[str]:
    from sqlalchemy import text

    from app.db.rls import system_session

    async with system_db() as s, s.begin(), system_session(s):
        rows = (
            await s.execute(text("SELECT tenant_id FROM tenant_vault_keys ORDER BY tenant_id"))
        ).fetchall()
    return [str(r[0]) for r in rows]


async def compact_tenant_keys(
    *,
    tenant_db: Any,
    system_db: Any = None,
    redis: Any = None,
    tenant_ids: list[str] | None = None,
    dry_run: bool = False,
    batch_size: int = 200,
    min_age_seconds: float = 120.0,
    progress: Callable[[dict[str, Any]], None] | None = None,
) -> dict[str, Any]:
    """Compact every (or the given) tenant's keyring; see the module docstring."""
    result: dict[str, Any] = {"dry_run": dry_run, "status": "complete", "tenants": []}
    if tenant_ids is None:
        tenant_ids = await _tenants_with_keys(system_db or tenant_db)
    for tenant_id in tenant_ids:
        rep = TenantCompaction(tenant_id)
        try:
            row = await _keyring_row(tenant_db, tenant_id)
            if row is None:
                rep.status = "no_tenant_key"
                continue
            wrapped, age_seconds = row
            keys = _unwrap_keys(wrapped)
            rep.previous_keys = len(keys) - 1
            if rep.previous_keys == 0:
                rep.status = "nothing_to_compact"
                continue
            if age_seconds < min_age_seconds:
                rep.status = "key_replaced_too_recently"
                continue
            sealer = _Sealer(keys, rep)
            await _pg_pass(tenant_db, tenant_id, sealer, dry_run, batch_size)
            if redis is not None:
                await _redis_pass(redis, tenant_id, sealer, dry_run)
            if rep.unreadable or rep.lost_races:
                rep.status = "kept_previous_keys"
            elif dry_run:
                rep.status = "would_drop_previous_keys"
            elif await _drop_previous(tenant_db, tenant_id, wrapped, keys[0]):
                rep.keys_dropped = rep.previous_keys
                rep.status = "compacted"
            else:
                rep.status = "kept_previous_keys"  # keyring changed meanwhile
                rep.lost_races += 1
        except (TenantVaultError, Exception) as exc:
            rep.status = "error"
            rep.errors.append(f"{type(exc).__name__}: {exc}"[:300])
        finally:
            result["tenants"].append(vars(rep))
            if progress is not None:
                progress(vars(rep))
    unfinished = ("error", "kept_previous_keys", "key_replaced_too_recently")
    bad = [t for t in result["tenants"] if t["status"] in unfinished]
    if bad:
        result["status"] = "incomplete"
    elif dry_run:
        result["status"] = "dry_run"
    return result
