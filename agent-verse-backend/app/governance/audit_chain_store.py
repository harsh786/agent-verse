"""Postgres-backed tamper-evident audit chain.

Persists the hash chain (app.governance.audit_chain.compute_hash) so tamper
evidence survives restarts. Per-tenant monotonic ``seq``; the (tenant, seq)
primary key makes concurrent appends race-safe (a duplicate seq fails and the
caller may retry). All statements run under tenant RLS.
"""

from __future__ import annotations

import asyncio
import json
import random
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

from app.db.rls import sqlalchemy_rls_context
from app.governance.audit_chain import _GENESIS, compute_hash

# Bounded retry for the (tenant_id, seq) PK race: two replicas (or two requests
# on one replica) read the same tip and both try to INSERT seq N+1; the loser
# gets a unique violation. Re-reading the tip and re-hashing is always correct
# because nothing was committed by the losing transaction.
APPEND_MAX_ATTEMPTS = 6


class AuditChainAppendError(RuntimeError):
    """The record could not be appended after the bounded retries."""


class PersistentAuditChain:
    def __init__(self, session_factory: Any) -> None:
        self._sf = session_factory

    async def append(self, tenant_id: str, payload: dict[str, Any]) -> dict[str, Any]:
        """Append one record, retrying on a concurrent-append seq collision.

        Raises :class:`AuditChainAppendError` when every attempt collided, and
        re-raises any non-conflict DB error — a lost audit record must never be
        silent.
        """
        last_exc: Exception | None = None
        for attempt in range(APPEND_MAX_ATTEMPTS):
            try:
                return await self._append_once(tenant_id, payload)
            except IntegrityError as exc:
                last_exc = exc
                # Jittered backoff so the colliding writers spread out.
                await asyncio.sleep(random.uniform(0, 0.01 * (2**attempt)))
        raise AuditChainAppendError(
            f"audit chain append for tenant {tenant_id} lost {APPEND_MAX_ATTEMPTS} "
            "consecutive seq races"
        ) from last_exc

    async def _append_once(self, tenant_id: str, payload: dict[str, Any]) -> dict[str, Any]:
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


__all__ = ["AuditChainAppendError", "PersistentAuditChain"]
