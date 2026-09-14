"""Postgres-backed tamper-evident audit chain.

Persists the hash chain (app.governance.audit_chain.compute_hash) so tamper
evidence survives restarts. Per-tenant monotonic ``seq``; the (tenant, seq)
primary key makes concurrent appends race-safe (a duplicate seq fails and the
caller may retry). All statements run under tenant RLS.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import text

from app.db.rls import sqlalchemy_rls_context
from app.governance.audit_chain import _GENESIS, compute_hash


class PersistentAuditChain:
    def __init__(self, session_factory: Any) -> None:
        self._sf = session_factory

    async def append(self, tenant_id: str, payload: dict[str, Any]) -> dict[str, Any]:
        """Append one record to the tenant's chain; returns the stored record."""
        at = datetime.now(UTC)
        at_iso = at.isoformat()
        async with self._sf() as session, session.begin(), sqlalchemy_rls_context(
            session, tenant_id
        ):
            row = (
                await session.execute(
                    text(
                        "SELECT seq, record_hash FROM audit_chain "
                        "WHERE tenant_id = :t ORDER BY seq DESC LIMIT 1"
                    ),
                    {"t": tenant_id},
                )
            ).one_or_none()
            seq = (row[0] + 1) if row else 0
            prev = row[1] if row else _GENESIS
            record_hash = compute_hash(prev, payload, seq=seq, at=at_iso)
            await session.execute(
                text(
                    "INSERT INTO audit_chain "
                    "(tenant_id, seq, occurred_at, payload, prev_hash, record_hash) VALUES "
                    "(:t, :seq, :at, CAST(:payload AS jsonb), :prev, :rh)"
                ),
                {
                    "t": tenant_id,
                    "seq": seq,
                    "at": at,
                    "payload": json.dumps(payload, default=str),
                    "prev": prev,
                    "rh": record_hash,
                },
            )
        return {"seq": seq, "at": at_iso, "prev_hash": prev, "record_hash": record_hash}

    async def verify(self, tenant_id: str) -> tuple[bool, int | None]:
        """Recompute the tenant's chain. Returns (ok, first_broken_seq_or_None)."""
        async with self._sf() as session, session.begin(), sqlalchemy_rls_context(
            session, tenant_id
        ):
            rows = (
                await session.execute(
                    text(
                        "SELECT seq, occurred_at, payload, prev_hash, record_hash "
                        "FROM audit_chain WHERE tenant_id = :t ORDER BY seq ASC"
                    ),
                    {"t": tenant_id},
                )
            ).all()
        prev = _GENESIS
        for seq, occurred_at, payload, prev_hash, record_hash in rows:
            payload_d = json.loads(payload) if isinstance(payload, str) else payload
            at_iso = occurred_at.isoformat() if hasattr(occurred_at, "isoformat") else str(
                occurred_at
            )
            expected = compute_hash(prev, payload_d, seq=seq, at=at_iso)
            if prev_hash != prev or record_hash != expected:
                return False, seq
            prev = record_hash
        return True, None


__all__ = ["PersistentAuditChain"]
