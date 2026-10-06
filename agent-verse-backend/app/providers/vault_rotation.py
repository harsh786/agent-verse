"""Offline vault master-key rotation over every ciphertext store (``agentverse vault-rotate``).

Every value the platform vault ever wrote is re-encrypted from the old key (the
current key plus ``VAULT_PREVIOUS_MASTER_KEYS``) to the new one, so the previous
keys can be retired afterwards:

Postgres, per tenant (each batch in one transaction under that tenant's RLS
context, with an explicit ``tenant_id`` predicate as well):

* ``tenant_vault_keys.wrapped_key`` — tenant envelope keys (re-wrapped);
* ``tenant_llm_configs.encrypted_key`` — tenant LLM API keys;
* ``oauth_tokens.access_token / refresh_token`` — connector OAuth tokens;
* ``schedules.webhook_signature_secret_enc / _prev_enc`` — trigger secrets;
* ``memory_records.sealed_content`` — sealed (confidential) memories;
* ``source_configs.connection_config`` secret values — ingestion source credentials;
* ``agent_credentials.private_key_ref`` — agent signing keys;
* ``auction_registry.sealed_keys`` — sealed-bid auction keys;
* ``mcp_credentials.encrypted_value`` — durable connector secrets;
* ``workflows.definition`` / ``workflow_definitions.definition_json`` /
  ``workflow_definition_versions.definition_json`` ``enc:v1:`` values — workflow
  webhook HMAC secrets (B2-OPEN-1);
* ``channel_tenant_mappings.channel_config`` ``*_enc`` values — messaging-gateway
  binding secrets (``secret_enc`` / ``outbound_token_enc`` / ``verify_token_enc``,
  DEF-3) of tenants without an envelope key (B2-GAP-2);
* ``tenant_email_settings.smtp_secret_enc`` — tenant-owned SMTP sender secrets
  (a02-F036-02).

Redis: connector secrets (``mcp:connector_secrets:*``), OAuth tokens copied into
connector configs (``mcp:servers:*``) and the tenant LLM-config cache
(``llm_config:*``).

Properties:

* **idempotent** — a value that already opens with the new key is left alone,
  so a re-run (or a resumed run) never double-encrypts;
* **resumable** — after every committed batch the position is checkpointed in
  ``vault_rotation_checkpoints`` (keyed by the new key's fingerprint); a re-run
  with the same new key continues from there;
* **batched** — keyset pagination, ``batch_size`` rows per transaction;
* **dry-run** — counts what would change, writes nothing;
* **honest** — a value that opens with neither key is counted as failed and
  the run reports ``failed`` (previous keys must stay configured);
* values encrypted with a *tenant* envelope key (``tv1:``) are not touched:
  only their wrapping key (``tenant_vault_keys``) depends on the master key.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from app.providers.tenant_vault import TENANT_CIPHER_PREFIX

_log = logging.getLogger(__name__)
_SOURCE_PREFIX = "enc:v1:"


class _OpenFailedError(Exception):
    """A value opens with neither the new nor the old key."""


@dataclass
class StoreReport:
    rows_scanned: int = 0
    rows_written: int = 0
    rotated: int = 0
    already_current: int = 0
    tenant_key: int = 0
    failed: int = 0

    def add(self, other: StoreReport) -> None:
        for k in vars(self):
            setattr(self, k, getattr(self, k) + getattr(other, k))


@dataclass(frozen=True)
class PgStore:
    name: str
    table: str
    pk: str
    columns: tuple[str, ...]
    prefix: str = ""
    source_json: bool = False
    # Column recording which platform key sealed the row (BYOK-2): set to the new
    # key's fingerprint on every row that opens with it after this run.
    fingerprint_column: str = ""
    # tenant_id is a UUID column (the workflow engine tables): the tenant id is
    # matched as a UUID, and a tenant whose id is not a UUID has no rows there.
    uuid_tenant: bool = False
    # With ``source_json``: the vault values are the (unprefixed) ``*_enc`` keys
    # of the JSON (gateway bindings), not ``enc:v1:`` secret-named keys.
    enc_fields: bool = False


PG_STORES: tuple[PgStore, ...] = (
    PgStore("tenant_vault_keys", "tenant_vault_keys", "tenant_id", ("wrapped_key",)),
    PgStore(
        "tenant_llm_configs",
        "tenant_llm_configs",
        "tenant_id",
        ("encrypted_key",),
        fingerprint_column="vault_key_fingerprint",
    ),
    PgStore("oauth_tokens", "oauth_tokens", "id", ("access_token", "refresh_token")),
    PgStore(
        "trigger_secrets",
        "schedules",
        "id",
        ("webhook_signature_secret_enc", "webhook_signature_secret_prev_enc"),
    ),
    PgStore("sealed_memories", "memory_records", "id", ("sealed_content",), prefix="enc:v1:"),
    PgStore(
        "source_credentials", "source_configs", "id", ("connection_config",), source_json=True
    ),
    PgStore(
        "agent_signing_keys", "agent_credentials", "id", ("private_key_ref",), prefix="vault:v1:"
    ),
    PgStore("auction_keys", "auction_registry", "id", ("sealed_keys",)),
    # Durable connector secrets (SECRET-01): composite key, so the keyset
    # position is "<server_id>\x1f<secret_key>" (per tenant, a handful of rows).
    PgStore(
        "connector_secrets_pg",
        "mcp_credentials",
        "(server_id || chr(31) || secret_key)",
        ("encrypted_value",),
    ),
    # Workflow webhook HMAC secrets (B2-OPEN-1): ``enc:v1:`` values inside the
    # definition JSON — the builder row, its run-engine mirror and its snapshots.
    PgStore("workflow_webhook_secrets", "workflows", "id", ("definition",), source_json=True),
    PgStore(
        "workflow_definition_secrets",
        "workflow_definitions",
        "id::text",
        ("definition_json",),
        source_json=True,
        uuid_tenant=True,
    ),
    PgStore(
        "workflow_version_secrets",
        "workflow_definition_versions",
        "id::text",
        ("definition_json",),
        source_json=True,
        uuid_tenant=True,
    ),
    # Messaging-gateway binding secrets (DEF-3, B2-GAP-2): ``channel_config.*_enc``
    # — platform-vault ciphertext unless the tenant has an envelope key (``tv1:``,
    # only its wrapping key depends on the master key). Last in the list, so a
    # rotation checkpointed before this store existed resumes onto it.
    PgStore(
        "channel_binding_secrets",
        "channel_tenant_mappings",
        "id",
        ("channel_config",),
        source_json=True,
        enc_fields=True,
    ),
    # Tenant-owned SMTP sender secrets (a02-F036-02). Appended last so a rotation
    # checkpointed before this store existed resumes onto it.
    PgStore(
        "tenant_smtp_secrets",
        "tenant_email_settings",
        "tenant_id",
        ("smtp_secret_enc",),
        fingerprint_column="vault_key_fingerprint",
    ),
)
REDIS_STORES: tuple[str, ...] = ("connector_secrets", "connector_oauth_copies", "llm_config_cache")


# ── value codecs ──────────────────────────────────────────────────────────────


def _rotate_ciphertext(ct: str, old: Any, new: Any, report: StoreReport) -> str | None:
    """New ciphertext for ``ct``, or None when it needs no change."""
    if ct.startswith(TENANT_CIPHER_PREFIX):
        report.tenant_key += 1
        return None
    try:
        new.decrypt(ct)
        report.already_current += 1
        return None
    except Exception:
        pass
    try:
        plain = old.decrypt(ct)
    except Exception as exc:
        report.failed += 1
        raise _OpenFailedError(type(exc).__name__) from exc
    report.rotated += 1
    return str(new.encrypt(plain))


def _rotate_prefixed(value: Any, prefix: str, old: Any, new: Any, report: StoreReport) -> Any:
    if not isinstance(value, str) or not value:
        return None
    if prefix:
        if not value.startswith(prefix):
            return None  # not sealed by the vault (legacy / empty marker)
        inner = _rotate_ciphertext(value[len(prefix) :], old, new, report)
        return None if inner is None else prefix + inner
    return _rotate_ciphertext(value, old, new, report)


def _rotate_source_config(config: Any, old: Any, new: Any, report: StoreReport) -> Any:
    """connection_config with every ``enc:v1:`` secret re-encrypted (None = unchanged)."""
    from app.ingestion.source_secrets import is_secret_key

    if isinstance(config, list):
        # A list of configs (workflow definition ``triggers``).
        items = [_rotate_source_config(item, old, new, report) for item in config]
        if all(item is None for item in items):
            return None
        return [orig if item is None else item for orig, item in zip(config, items, strict=True)]
    if not isinstance(config, dict):
        return None
    changed = False
    out: dict[str, Any] = {}
    for key, value in config.items():
        new_value: Any = None
        if is_secret_key(key) and isinstance(value, str):
            try:
                new_value = _rotate_prefixed(value, _SOURCE_PREFIX, old, new, report)
            except _OpenFailedError:
                new_value = None
        elif isinstance(value, dict | list):
            new_value = _rotate_source_config(value, old, new, report)
        if new_value is not None:
            out[key] = new_value
            changed = True
        else:
            out[key] = value
    return out if changed else None


def _rotate_enc_fields(config: Any, old: Any, new: Any, report: StoreReport) -> Any:
    """A JSON config with every ``*_enc`` vault value re-encrypted (None = unchanged).

    ``tv1:`` (tenant envelope) values are left alone; one that opens with neither
    key is counted as failed and kept as it is."""
    if isinstance(config, list):
        items = [_rotate_enc_fields(item, old, new, report) for item in config]
        if all(item is None for item in items):
            return None
        return [orig if item is None else item for orig, item in zip(config, items, strict=True)]
    if not isinstance(config, dict):
        return None
    changed = False
    out: dict[str, Any] = {}
    for key, value in config.items():
        new_value: Any = None
        if str(key).endswith("_enc") and isinstance(value, str):
            try:
                new_value = _rotate_prefixed(value, "", old, new, report)
            except _OpenFailedError:
                new_value = None
        elif isinstance(value, dict | list):
            new_value = _rotate_enc_fields(value, old, new, report)
        out[key] = value if new_value is None else new_value
        changed = changed or new_value is not None
    return out if changed else None


# ── Postgres stores ───────────────────────────────────────────────────────────


async def _tenant_ids(system_db: Any) -> list[str]:
    from sqlalchemy import text

    async with system_db() as session:
        rows = (await session.execute(text("SELECT id FROM tenants ORDER BY id"))).fetchall()
    return [str(r[0]) for r in rows]


async def _rotate_pg_batch(
    tenant_db: Any,
    store: PgStore,
    tenant_id: str,
    after: str,
    batch_size: int,
    old: Any,
    new: Any,
    dry_run: bool,
) -> tuple[StoreReport, str | None]:
    """One batch of one tenant's rows; returns (report, last pk or None when done)."""
    from sqlalchemy import text

    from app.db.rls import sqlalchemy_rls_context

    report = StoreReport()
    tenant_param = tenant_id
    if store.uuid_tenant:
        import uuid as _uuid

        try:
            tenant_param = str(_uuid.UUID(str(tenant_id)))
        except (ValueError, TypeError, AttributeError):
            return report, None  # a non-UUID tenant has no rows in a UUID-keyed table
    tenant_match = "tenant_id = CAST(:t AS uuid)" if store.uuid_tenant else "tenant_id = :t"
    fp_col = store.fingerprint_column
    cols = ", ".join((*store.columns, fp_col) if fp_col else store.columns)
    async with (
        tenant_db() as session,
        session.begin(),
        sqlalchemy_rls_context(session, tenant_id),
    ):
        rows = (
            await session.execute(
                text(
                    f"SELECT {store.pk}, {cols} FROM {store.table} "
                    f"WHERE {tenant_match} AND {store.pk} > :after "
                    f"ORDER BY {store.pk} LIMIT :n"
                ),
                {"t": tenant_param, "after": after, "n": batch_size},
            )
        ).fetchall()
        for row in rows:
            report.rows_scanned += 1
            updates: dict[str, Any] = {}
            values = row[1 : 1 + len(store.columns)]
            opened = True
            for col, value in zip(store.columns, values, strict=True):
                if store.source_json:
                    data = json.loads(value) if isinstance(value, str) else value
                    codec = _rotate_enc_fields if store.enc_fields else _rotate_source_config
                    rotated = codec(data, old, new, report)
                    if rotated is not None:
                        updates[col] = json.dumps(rotated)
                    continue
                try:
                    rotated = _rotate_prefixed(value, store.prefix, old, new, report)
                except _OpenFailedError:
                    opened = False
                    continue
                if rotated is not None:
                    updates[col] = rotated
            if fp_col and opened and row[-1] != new.fingerprint():
                # tv1 rows count too: their tenant key's wrapper (tenant_vault_keys,
                # rotated first) is under the new platform key after this run.
                updates[fp_col] = new.fingerprint()
            if updates and not dry_run:
                assignments = ", ".join(
                    f"{c} = CAST(:{c} AS jsonb)" if store.source_json else f"{c} = :{c}"
                    for c in updates
                )
                await session.execute(
                    text(
                        f"UPDATE {store.table} SET {assignments} "
                        f"WHERE {tenant_match} AND {store.pk} = :pk"
                    ),
                    {**updates, "t": tenant_param, "pk": row[0]},
                )
                report.rows_written += 1
    last = str(rows[-1][0]) if len(rows) == batch_size else None
    return report, last


# ── Redis stores ──────────────────────────────────────────────────────────────


async def _rotate_redis(redis: Any, old: Any, new: Any, dry_run: bool) -> dict[str, StoreReport]:
    reports = {name: StoreReport() for name in REDIS_STORES}
    writes: list[tuple[Any, str]] = []

    async def _raw(match: str, rep: StoreReport) -> None:
        async for key in redis.scan_iter(match=match, count=200):
            raw = await redis.get(key)
            if not raw:
                continue
            rep.rows_scanned += 1
            value = raw.decode() if isinstance(raw, bytes) else str(raw)
            try:
                rotated = _rotate_prefixed(value, "", old, new, rep)
            except _OpenFailedError:
                continue
            if rotated is not None:
                writes.append((key, rotated))
                rep.rows_written += 1

    async def _json(
        match: str, fields: tuple[str, ...], nested: str, rep: StoreReport, fp_field: str = ""
    ) -> None:
        async for key in redis.scan_iter(match=match, count=200):
            raw = await redis.get(key)
            if not raw:
                continue
            try:
                doc = json.loads(raw.decode() if isinstance(raw, bytes) else raw)
            except ValueError:
                continue
            target = doc.get(nested) if nested else doc
            if not isinstance(target, dict):
                continue
            rep.rows_scanned += 1
            changed = False
            opened = True
            for fld in fields:
                try:
                    rotated = _rotate_prefixed(target.get(fld), "", old, new, rep)
                except _OpenFailedError:
                    opened = False
                    continue
                if rotated is not None:
                    target[fld] = rotated
                    changed = True
            if fp_field and opened and target.get(fp_field) != new.fingerprint():
                target[fp_field] = new.fingerprint()
                changed = True
            if changed:
                writes.append((key, json.dumps(doc)))
                rep.rows_written += 1

    await _raw("mcp:connector_secrets:*", reports["connector_secrets"])
    await _json(
        "mcp:servers:*",
        ("_encrypted_access_token", "_encrypted_refresh_token"),
        "auth_config",
        reports["connector_oauth_copies"],
    )
    await _json(
        "llm_config:*",
        ("encrypted_key",),
        "",
        reports["llm_config_cache"],
        fp_field="vault_key_fingerprint",
    )
    if writes and not dry_run:
        for i in range(0, len(writes), 200):
            pipe = redis.pipeline()
            for key, value in writes[i : i + 200]:
                pipe.set(key, value)
            await pipe.execute()
    if not dry_run:
        # Short-TTL read caches of the durable connector stores (ciphertext /
        # configs): dropped, not rewritten (a SET would strip their TTL); they
        # refill from Postgres, which this run re-encrypted.
        for pattern in ("mcp:secretcache:v1:*", "mcp:cfgcache:v1:*"):
            async for key in redis.scan_iter(match=pattern, count=500):
                await redis.delete(key)
    return reports


# ── checkpoints ───────────────────────────────────────────────────────────────


async def _load_checkpoint(system_db: Any, rotation_id: str) -> dict[str, Any] | None:
    from sqlalchemy import text

    from app.db.rls import system_session

    async with system_db() as session, session.begin(), system_session(session):
        row = (
            await session.execute(
                text(
                    "SELECT position, report, status FROM vault_rotation_checkpoints "
                    "WHERE rotation_id = :r"
                ),
                {"r": rotation_id},
            )
        ).fetchone()
    if row is None:
        return None
    position = row[0] if isinstance(row[0], dict) else json.loads(row[0] or "{}")
    report = row[1] if isinstance(row[1], dict) else json.loads(row[1] or "{}")
    return {"position": position, "report": report, "status": row[2]}


async def _save_checkpoint(
    system_db: Any, rotation_id: str, position: dict[str, Any], report: dict[str, Any], status: str
) -> None:
    from sqlalchemy import text

    from app.db.rls import system_session

    async with system_db() as session, session.begin(), system_session(session):
        await session.execute(
            text(
                "INSERT INTO vault_rotation_checkpoints (rotation_id, position, report, status) "
                "VALUES (:r, CAST(:p AS jsonb), CAST(:rep AS jsonb), :s) "
                "ON CONFLICT (rotation_id) DO UPDATE SET position = EXCLUDED.position, "
                "report = EXCLUDED.report, status = EXCLUDED.status, updated_at = NOW()"
            ),
            {"r": rotation_id, "p": json.dumps(position), "rep": json.dumps(report), "s": status},
        )


async def _record_key_version(system_db: Any, rotation_id: str) -> None:
    import uuid

    from sqlalchemy import text

    from app.db.rls import system_session

    async with system_db() as session, session.begin(), system_session(session):
        await session.execute(
            text(
                "UPDATE vault_key_versions SET is_current = FALSE, retired_at = NOW() "
                "WHERE is_current = TRUE"
            )
        )
        await session.execute(
            text(
                "INSERT INTO vault_key_versions (id, key_hash, activated_at, is_current) "
                "VALUES (:id, :hash, NOW(), TRUE)"
            ),
            {"id": uuid.uuid4().hex, "hash": rotation_id},
        )


# ── driver ────────────────────────────────────────────────────────────────────


@dataclass
class _Run:
    reports: dict[str, StoreReport] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        return {name: vars(rep) for name, rep in self.reports.items()}


async def rotate_all_stores(
    *,
    old: Any,
    new: Any,
    rotation_id: str,
    system_db: Any = None,
    tenant_db: Any = None,
    redis: Any = None,
    dry_run: bool = False,
    batch_size: int = 200,
    progress: Callable[[dict[str, Any]], None] | None = None,
    pg_stores: tuple[PgStore, ...] = PG_STORES,
    record_key_version: bool = True,
) -> dict[str, Any]:
    """Rotate every store; see the module docstring. Returns the run report.

    ``pg_stores`` / ``record_key_version`` let a sibling rotation reuse the engine
    over its own stores (``agentverse mfa-rotate``: SECRET_KEY-sealed MFA secrets,
    which are not vault ciphertext and have no vault key version).
    """
    run = _Run({s.name: StoreReport() for s in pg_stores})
    if redis is not None:
        for name in REDIS_STORES:
            run.reports[name] = StoreReport()
    result: dict[str, Any] = {
        "status": "failed",
        "rotation_id": rotation_id,
        "dry_run": dry_run,
        "resumed": False,
        "stores": {},
        "errors": [],
        "previous_keys_retirable": False,
    }
    tenant_db = tenant_db or system_db
    position: dict[str, Any] = {}
    already_recorded = False
    if system_db is not None and not dry_run:
        try:
            checkpoint = await _load_checkpoint(system_db, rotation_id)
        except Exception as exc:
            result["errors"].append(f"checkpoint read: {type(exc).__name__}: {exc}"[:300])
            return result
        if checkpoint is not None:
            result["resumed"] = True
            position = dict(checkpoint["position"] or {})
            for name, values in (checkpoint["report"] or {}).items():
                if name in run.reports:
                    run.reports[name] = StoreReport(**values)
            if checkpoint["status"] == "complete":
                position = {"store": "__done__"}
                # A store added after this rotation completed (e.g. gateway binding
                # secrets, B2-GAP-2) still holds old-key values: rotate from the
                # first such store on (the ones after it re-scan idempotently).
                done = set((checkpoint["report"] or {}).keys())
                missing = [s.name for s in pg_stores if s.name not in done]
                if missing:
                    position = {"store": missing[0]}
                    already_recorded = True

    def _emit(store: str, tenant: str | None) -> None:
        if progress is not None:
            progress({"store": store, "tenant": tenant, "report": vars(run.reports[store])})

    # Postgres, store by store, tenant by tenant, batch by batch.
    if system_db is not None and position.get("store") != "__done__":
        try:
            tenants = await _tenant_ids(system_db)
        except Exception as exc:
            result["errors"].append(f"tenants: {type(exc).__name__}: {exc}"[:300])
            result["stores"] = run.as_dict()
            return result
        names = [s.name for s in pg_stores]
        start_store = names.index(position["store"]) if position.get("store") in names else 0
        for store in pg_stores[start_store:]:
            at_store = store.name == position.get("store")
            for tenant in tenants:
                if at_store and (
                    tenant < position.get("tenant", "")
                    or (tenant == position.get("tenant") and position.get("tenant_done"))
                ):
                    continue  # already rotated before the interruption
                after = ""
                if at_store and tenant == position.get("tenant"):
                    after = str(position.get("last_key") or "")
                while True:
                    try:
                        batch, last = await _rotate_pg_batch(
                            tenant_db, store, tenant, after, batch_size, old, new, dry_run
                        )
                        run.reports[store.name].add(batch)
                        if not dry_run:
                            # Checkpoint the committed batch (tenant_done when finished).
                            await _save_checkpoint(
                                system_db,
                                rotation_id,
                                {
                                    "store": store.name,
                                    "tenant": tenant,
                                    "last_key": last or "",
                                    "tenant_done": last is None,
                                },
                                run.as_dict(),
                                "running",
                            )
                    except Exception as exc:
                        result["errors"].append(
                            f"{store.name}/{tenant}: {type(exc).__name__}: {exc}"[:300]
                        )
                        result["stores"] = run.as_dict()
                        return result  # checkpoint stays at the last committed batch
                    if last is None:
                        break
                    after = last
                _emit(store.name, tenant)
            position = {}

    if redis is not None:
        try:
            for name, rep in (await _rotate_redis(redis, old, new, dry_run)).items():
                run.reports[name] = rep
                _emit(name, None)
        except Exception as exc:
            result["errors"].append(f"redis: {type(exc).__name__}: {exc}"[:300])
            result["stores"] = run.as_dict()
            return result

    result["stores"] = run.as_dict()
    failed = sum(r.failed for r in run.reports.values())
    if dry_run:
        result["status"] = "dry_run"
        result["would_rotate"] = sum(r.rotated for r in run.reports.values())
        result["unreadable"] = failed
        return result
    if failed:
        result["errors"].append(f"{failed} value(s) open with neither the new nor the old key")
        return result
    if system_db is not None:
        try:
            if record_key_version and not already_recorded:
                await _record_key_version(system_db, rotation_id)
            await _save_checkpoint(
                system_db, rotation_id, {"store": "__done__"}, run.as_dict(), "complete"
            )
        except Exception as exc:
            result["errors"].append(f"finalize: {type(exc).__name__}: {exc}"[:300])
            return result
    result["status"] = "complete"
    result["previous_keys_retirable"] = True
    return result
