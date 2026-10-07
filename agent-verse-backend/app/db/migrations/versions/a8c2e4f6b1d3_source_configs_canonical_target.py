"""source_configs.canonical_target_hash — one Source per target per KB collection.

Registering the same upstream data (the same MongoDB collection, bucket prefix,
SQL table set, feed ...) a second time into the same knowledge collection was
accepted, and its sync indexed every document again. ``POST/PATCH /sources`` now
refuse it (409); this column + partial unique index make the refusal race-free:
of two concurrent creates of the same target exactly one row can exist.

``canonical_target_hash`` is the SHA-256 of the secret-free canonical target
(``app.ingestion.source_identity``). NULL means "no comparable identity" (a
connector whose account is named only by its token) or "not back-filled"; NULL
rows are never constrained, and the API still compares them by decrypting them.

Back-fill: every existing row's key is computed from its config. Endpoint fields
such as a MongoDB ``uri`` or a Postgres ``dsn`` are vault-encrypted at rest, so
the platform vault (and, for a tenant with its own envelope key, that key) is
used when available; a row that cannot be opened stays NULL (logged, never
failing the upgrade). Existing duplicates — rows of one tenant and collection
with the same key — keep the key on the oldest row only; the others stay NULL
and are logged with their ids so an operator can delete the redundant Source.

Revision ID: a8c2e4f6b1d3
Revises: a4c6e8f0b2d1
Create Date: 2026-10-07
"""

from __future__ import annotations

import json
import logging
from collections.abc import Sequence
from typing import Any

from alembic import op
from sqlalchemy import text

revision: str = "a8c2e4f6b1d3"
down_revision: str | Sequence[str] | None = "a4c6e8f0b2d1"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_INDEX = "uq_source_configs_canonical_target"
_log = logging.getLogger("alembic.runtime.migration")


def _forced(bind: Any, table: str) -> bool:
    row = bind.execute(
        text("SELECT relforcerowsecurity FROM pg_class WHERE oid = to_regclass(:t)"),
        {"t": table},
    ).first()
    return bool(row and row[0])


def _tenant_vaults(bind: Any) -> dict[str, Any]:
    """tenant_id → its envelope vault (only tenants whose key can be unwrapped)."""
    if bind.execute(text("SELECT to_regclass('tenant_vault_keys')")).scalar() is None:
        return {}
    from app.providers.tenant_vault import _unwrap

    vaults: dict[str, Any] = {}
    for tenant_id, wrapped in bind.execute(
        text("SELECT tenant_id, wrapped_key FROM tenant_vault_keys")
    ).all():
        try:
            vaults[str(tenant_id)] = _unwrap(str(wrapped))
        except Exception as exc:  # unwrap needs the platform vault key
            _log.warning(
                "canonical_target backfill: tenant %s envelope key not usable (%s)",
                tenant_id,
                type(exc).__name__,
            )
    return vaults


def _json(value: Any) -> Any:
    if isinstance(value, str | bytes):
        try:
            return json.loads(value)
        except ValueError:
            return None
    return value


def _plaintext_config(row: Any, vaults: dict[str, Any]) -> dict[str, Any] | None:
    """The row's decrypted connection_config, or None when a secret cannot be opened."""
    from app.ingestion.source_secrets import ENC_PREFIX, _decrypt

    cc = _json(row.connection_config)
    if not isinstance(cc, dict):
        cc = {}

    def encrypted(value: Any) -> bool:
        if isinstance(value, dict):
            return any(encrypted(v) for v in value.values())
        return isinstance(value, str) and value.startswith(ENC_PREFIX)

    if not encrypted(cc):
        return dict(cc)
    try:
        out, _reencrypt, failed = _decrypt(cc, vaults.get(str(row.tenant_id)))
    except Exception:
        return None
    return None if failed else out


def _backfill(bind: Any) -> None:
    from app.ingestion.source_identity import canonical_target_hash

    rows = bind.execute(
        text(
            "SELECT id, tenant_id, source_type, collection_id, connection_config, "
            "include_patterns, exclude_patterns FROM source_configs "
            "WHERE canonical_target_hash IS NULL ORDER BY created_at, id"
        )
    ).all()
    if not rows:
        return
    vaults = _tenant_vaults(bind)
    seen: dict[tuple[str, str, str], str] = {}
    updates: list[dict[str, str]] = []
    unreadable = 0
    for row in rows:
        cc = _plaintext_config(row, vaults)
        if cc is None:
            unreadable += 1
            continue
        target_hash = canonical_target_hash(
            str(row.source_type or ""),
            cc,
            include_patterns=_json(row.include_patterns) or (),
            exclude_patterns=_json(row.exclude_patterns) or (),
        )
        if target_hash is None:
            continue
        collection = str(row.collection_id or "")
        if collection:
            key = (str(row.tenant_id), collection, target_hash)
            first = seen.get(key)
            if first is not None:
                # An existing duplicate: left in place (no data is deleted by a
                # migration), unconstrained, and reported.
                _log.warning(
                    "canonical_target backfill: source %s duplicates source %s "
                    "(tenant %s, collection %s); left without a key — delete the "
                    "redundant source",
                    row.id,
                    first,
                    row.tenant_id,
                    collection,
                )
                continue
            seen[key] = str(row.id)
        updates.append({"id": str(row.id), "h": target_hash})
    if updates:
        bind.execute(
            text("UPDATE source_configs SET canonical_target_hash = :h WHERE id = :id"),
            updates,
        )
    if unreadable:
        _log.warning(
            "canonical_target backfill: %d source(s) left without a key (credentials "
            "not readable here); the API still compares them on create/update",
            unreadable,
        )


def upgrade() -> None:
    bind = op.get_bind()
    op.execute("SET LOCAL lock_timeout = '5s'")
    op.execute(
        "ALTER TABLE source_configs ADD COLUMN IF NOT EXISTS canonical_target_hash VARCHAR(64)"
    )
    # FORCE RLS would hide every row from the migration role (no tenant GUC):
    # lift it for the back-fill and restore it in the same transaction.
    forced = {t: _forced(bind, t) for t in ("source_configs", "tenant_vault_keys")}
    for table, was_forced in forced.items():
        if was_forced:
            op.execute(f"ALTER TABLE {table} NO FORCE ROW LEVEL SECURITY")
    _backfill(bind)  # a failure aborts the transaction, which restores FORCE too
    for table, was_forced in forced.items():
        if was_forced:
            op.execute(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY")
    # source_configs holds a handful of rows per tenant: a plain (transactional)
    # build is brief and keeps the back-fill and the guard atomic.
    op.execute(
        f"CREATE UNIQUE INDEX IF NOT EXISTS {_INDEX} "
        "ON source_configs (tenant_id, collection_id, canonical_target_hash) "
        "WHERE canonical_target_hash IS NOT NULL "
        "AND collection_id IS NOT NULL AND collection_id <> ''"
    )


def downgrade() -> None:
    op.execute(f"DROP INDEX IF EXISTS {_INDEX}")
    op.execute("ALTER TABLE source_configs DROP COLUMN IF EXISTS canonical_target_hash")
