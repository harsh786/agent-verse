"""Postgres-backed prospective memory — durable deferred intentions.

Same interface as the in-memory ProspectiveMemoryService so it drops into the
planner recall + scheduler unchanged, but intentions persist across restarts.
All statements run under tenant RLS; leasing uses fencing tokens + SKIP LOCKED.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import text

from app.db.rls import sqlalchemy_rls_context
from app.memory.prospective import ProspectiveMemory

_COLS = (
    "memory_id, tenant_id, intention, due_at, expires_at, state, source_goal_id, "
    "source_execution_id, policy_snapshot, classification, idempotency_key, attempts, "
    "fencing_token, lease_expires_at, result"
)
_TERMINAL = ("completed", "failed", "cancelled", "expired")


def _row(r: Any) -> ProspectiveMemory:
    def _j(v: Any) -> Any:
        return json.loads(v) if isinstance(v, str) else v

    return ProspectiveMemory(
        memory_id=r["memory_id"],
        tenant_id=r["tenant_id"],
        intention=r["intention"],
        due_at=r["due_at"],
        expires_at=r["expires_at"],
        state=r["state"],
        source_goal_id=r["source_goal_id"],
        source_execution_id=r["source_execution_id"],
        policy_snapshot=_j(r["policy_snapshot"]) or {},
        classification=r["classification"],
        idempotency_key=r["idempotency_key"],
        attempts=r["attempts"],
        fencing_token=r["fencing_token"],
        lease_expires_at=r["lease_expires_at"],
        result=_j(r["result"]),
    )


class PostgresProspectiveMemoryService:
    def __init__(self, session_factory: Any) -> None:
        self._sf = session_factory

    async def create(self, item: ProspectiveMemory) -> ProspectiveMemory:
        async with self._sf() as s, s.begin(), sqlalchemy_rls_context(s, item.tenant_id):
            await s.execute(
                text(
                    f"INSERT INTO prospective_memory ({_COLS}) VALUES "
                    "(:memory_id,:tenant_id,:intention,:due_at,:expires_at,:state,"
                    ":source_goal_id,:source_execution_id,CAST(:policy_snapshot AS jsonb),"
                    ":classification,:idempotency_key,:attempts,:fencing_token,"
                    ":lease_expires_at,CAST(:result AS jsonb)) "
                    "ON CONFLICT ON CONSTRAINT uq_prospective_mem_idem DO NOTHING"
                ),
                {
                    "memory_id": item.memory_id,
                    "tenant_id": item.tenant_id,
                    "intention": item.intention,
                    "due_at": item.due_at,
                    "expires_at": item.expires_at,
                    "state": item.state,
                    "source_goal_id": item.source_goal_id,
                    "source_execution_id": item.source_execution_id,
                    "policy_snapshot": json.dumps(item.policy_snapshot),
                    "classification": item.classification,
                    "idempotency_key": item.idempotency_key,
                    "attempts": item.attempts,
                    "fencing_token": item.fencing_token,
                    "lease_expires_at": item.lease_expires_at,
                    "result": json.dumps(item.result) if item.result is not None else None,
                },
            )
            row = (
                await s.execute(
                    text(
                        f"SELECT {_COLS} FROM prospective_memory "
                        "WHERE tenant_id=:t AND idempotency_key=:k"
                    ),
                    {"t": item.tenant_id, "k": item.idempotency_key},
                )
            ).mappings().one()
        return _row(row)

    async def get(self, tenant_id: str, memory_id: str) -> ProspectiveMemory | None:
        async with self._sf() as s, s.begin(), sqlalchemy_rls_context(s, tenant_id):
            row = (
                await s.execute(
                    text(
                        f"SELECT {_COLS} FROM prospective_memory "
                        "WHERE memory_id=:m AND tenant_id=:t"
                    ),
                    {"m": memory_id, "t": tenant_id},
                )
            ).mappings().one_or_none()
        return _row(row) if row else None

    async def list_active(
        self, tenant_id: str, *, now: datetime
    ) -> tuple[ProspectiveMemory, ...]:
        async with self._sf() as s, s.begin(), sqlalchemy_rls_context(s, tenant_id):
            rows = (
                await s.execute(
                    text(
                        f"SELECT {_COLS} FROM prospective_memory "
                        "WHERE tenant_id=:t "
                        "AND state NOT IN ('completed','failed','cancelled','expired') "
                        "AND expires_at > :now ORDER BY due_at ASC"
                    ),
                    {"t": tenant_id, "now": now},
                )
            ).mappings().all()
        return tuple(_row(r) for r in rows)

    async def lease_due(
        self, tenant_id: str, *, now: datetime, lease_duration: timedelta
    ) -> tuple[ProspectiveMemory, ...]:
        lease_until = now + lease_duration
        async with self._sf() as s, s.begin(), sqlalchemy_rls_context(s, tenant_id):
            # Expire overdue rows first, then atomically claim due ones.
            await s.execute(
                text(
                    "UPDATE prospective_memory SET state='expired' "
                    "WHERE tenant_id=:t AND state IN ('pending','leased') AND expires_at <= :now"
                ),
                {"t": tenant_id, "now": now},
            )
            rows = (
                await s.execute(
                    text(
                        "UPDATE prospective_memory SET state='leased', "
                        "attempts=attempts+1, fencing_token=fencing_token+1, "
                        "lease_expires_at=:lease_until "
                        "WHERE tenant_id=:t AND memory_id IN ("
                        "  SELECT memory_id FROM prospective_memory "
                        "  WHERE tenant_id=:t AND ((state='pending' AND due_at<=:now) "
                        "     OR (state='leased' AND lease_expires_at<=:now)) "
                        "  ORDER BY due_at ASC FOR UPDATE SKIP LOCKED"
                        f") RETURNING {_COLS}"
                    ),
                    {"t": tenant_id, "now": now, "lease_until": lease_until},
                )
            ).mappings().all()
        return tuple(_row(r) for r in rows)

    async def complete(
        self, tenant_id: str, memory_id: str, *, fencing_token: int,
        authorized: bool, result: dict[str, Any],
    ) -> ProspectiveMemory:
        if not authorized:
            raise PermissionError("prospective action no longer authorized")
        async with self._sf() as s, s.begin(), sqlalchemy_rls_context(s, tenant_id):
            row = (
                await s.execute(
                    text(
                        "UPDATE prospective_memory SET state='completed', "
                        "result=CAST(:result AS jsonb) "
                        "WHERE memory_id=:m AND tenant_id=:t "
                        "AND state='leased' AND fencing_token=:ft "
                        f"RETURNING {_COLS}"
                    ),
                    {
                        "m": memory_id,
                        "t": tenant_id,
                        "ft": fencing_token,
                        "result": json.dumps(result),
                    },
                )
            ).mappings().one_or_none()
        if row is None:
            raise RuntimeError("stale prospective-memory lease")
        return _row(row)

    async def cancel(self, tenant_id: str, memory_id: str, *, reason: str) -> ProspectiveMemory:
        async with self._sf() as s, s.begin(), sqlalchemy_rls_context(s, tenant_id):
            row = (
                await s.execute(
                    text(
                        "UPDATE prospective_memory SET state='cancelled', "
                        "result=CAST(:result AS jsonb) "
                        "WHERE memory_id=:m AND tenant_id=:t "
                        "AND state NOT IN ('completed','expired') "
                        f"RETURNING {_COLS}"
                    ),
                    {
                        "m": memory_id,
                        "t": tenant_id,
                        "result": json.dumps({"cancellation_reason": reason}),
                    },
                )
            ).mappings().one_or_none()
        if row is None:
            raise RuntimeError("terminal prospective memory cannot be cancelled")
        return _row(row)


__all__ = ["PostgresProspectiveMemoryService"]
