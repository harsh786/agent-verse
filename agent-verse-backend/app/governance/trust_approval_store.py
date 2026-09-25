"""Durable storage for multi-approver trust-governance requests.

`app/api/trust_governance.py` kept approvals in a module-level dict annotated
"In-memory for demo; production uses DB" — but no DB path existed, so approvals
vanished on restart and an approval granted on one replica did not exist for the
replica that served the next request.

Two properties this store exists to guarantee, neither of which an in-process
dict can provide in a multi-replica deployment:

* **Separation of duties.** `required_approvers` counts DISTINCT approvers.
  That is enforced by ``UNIQUE (request_id, approver_id)`` in the schema, so two
  concurrent approvals from the same person landing on different replicas cannot
  both count — the database rejects the second regardless of timing.
* **Atomic completion.** The "have we reached the threshold?" check runs against
  a row-locked request (``SELECT ... FOR UPDATE``), so two different approvers
  completing a request simultaneously cannot both observe a stale count.
"""

from __future__ import annotations

import datetime
from typing import Any

from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

from app.db.rls import sqlalchemy_rls_context


class DuplicateApproverError(Exception):
    """Raised when an approver tries to approve the same request twice."""


class ApprovalNotFoundError(Exception):
    """Raised when the request does not exist for this tenant."""


class ApprovalNotPendingError(Exception):
    """Raised when the request has already been resolved."""

    def __init__(self, status: str) -> None:
        super().__init__(status)
        self.status = status


def _iso(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, datetime.datetime):
        return value.isoformat()
    return str(value)


class TrustApprovalStore:
    """Postgres-backed store for ``trust_approval_requests`` / ``_votes``."""

    def __init__(self, db_factory: Any) -> None:
        self._db = db_factory

    async def create(
        self,
        *,
        tenant_id: str,
        approval_id: str,
        goal_id: str | None,
        step_description: str,
        tool_name: str,
        risk_level: str,
        required_approvers: int,
    ) -> None:
        async with self._db() as s, sqlalchemy_rls_context(s, tenant_id):
            await s.execute(
                text(
                    "INSERT INTO trust_approval_requests "
                    "(id, tenant_id, goal_id, step_description, tool_name, "
                    " risk_level, required_approvers) "
                    "VALUES (:id, :t, :g, :sd, :tool, :risk, :req)"
                ),
                {
                    "id": approval_id,
                    "t": tenant_id,
                    "g": goal_id,
                    "sd": step_description,
                    "tool": tool_name,
                    "risk": risk_level,
                    "req": max(1, int(required_approvers)),
                },
            )
            await s.commit()

    async def get(self, tenant_id: str, approval_id: str) -> dict[str, Any] | None:
        async with self._db() as s, sqlalchemy_rls_context(s, tenant_id):
            return await self._load(s, tenant_id, approval_id)

    async def list(
        self, tenant_id: str, status: str | None = None
    ) -> list[dict[str, Any]]:
        async with self._db() as s, sqlalchemy_rls_context(s, tenant_id):
            rows = (
                await s.execute(
                    text(
                        # CAST(...) not `:st::text` — SQLAlchemy's text() bind
                        # regex does not recognise `:name` followed by `::`, and
                        # asyncpg cannot infer the type of a bare NULL bind.
                        "SELECT id FROM trust_approval_requests "
                        "WHERE tenant_id = :t "
                        "  AND (CAST(:st AS text) IS NULL OR status = CAST(:st AS text)) "
                        "ORDER BY created_at DESC"
                    ),
                    {"t": tenant_id, "st": status},
                )
            ).fetchall()
            out = []
            for row in rows:
                loaded = await self._load(s, tenant_id, row[0])
                if loaded is not None:
                    out.append(loaded)
            return out

    async def add_vote(
        self, *, tenant_id: str, approval_id: str, approver_id: str, note: str
    ) -> dict[str, Any]:
        """Record one approval. Returns the refreshed request.

        Raises ApprovalNotFoundError / ApprovalNotPendingError /
        DuplicateApproverError.
        """
        async with self._db() as s, sqlalchemy_rls_context(s, tenant_id):
            row = (
                await s.execute(
                    text(
                        "SELECT status, required_approvers FROM trust_approval_requests "
                        "WHERE tenant_id = :t AND id = :id FOR UPDATE"
                    ),
                    {"t": tenant_id, "id": approval_id},
                )
            ).fetchone()
            if row is None:
                raise ApprovalNotFoundError(approval_id)
            if row[0] != "pending":
                raise ApprovalNotPendingError(str(row[0]))

            try:
                await s.execute(
                    text(
                        "INSERT INTO trust_approval_votes "
                        "(request_id, tenant_id, approver_id, action, note) "
                        "VALUES (:r, :t, :a, 'approved', :n)"
                    ),
                    {"r": approval_id, "t": tenant_id, "a": approver_id, "n": note},
                )
                await s.flush()
            except IntegrityError as exc:
                await s.rollback()
                raise DuplicateApproverError(approver_id) from exc

            count = (
                await s.execute(
                    text(
                        "SELECT count(*) FROM trust_approval_votes "
                        "WHERE tenant_id = :t AND request_id = :r"
                    ),
                    {"t": tenant_id, "r": approval_id},
                )
            ).scalar_one()

            if int(count) >= int(row[1]):
                await s.execute(
                    text(
                        "UPDATE trust_approval_requests "
                        "SET status = 'approved', resolved_at = NOW() "
                        "WHERE tenant_id = :t AND id = :id AND status = 'pending'"
                    ),
                    {"t": tenant_id, "id": approval_id},
                )
            refreshed = await self._load(s, tenant_id, approval_id)
            await s.commit()
            assert refreshed is not None
            return refreshed

    async def reject(
        self, *, tenant_id: str, approval_id: str, reason: str, rejected_by: str
    ) -> None:
        async with self._db() as s, sqlalchemy_rls_context(s, tenant_id):
            result = await s.execute(
                text(
                    "UPDATE trust_approval_requests "
                    "SET status = 'rejected', rejection_reason = :why, "
                    "    rejected_by = :who, resolved_at = NOW() "
                    "WHERE tenant_id = :t AND id = :id"
                ),
                {"t": tenant_id, "id": approval_id, "why": reason, "who": rejected_by},
            )
            if result.rowcount == 0:
                raise ApprovalNotFoundError(approval_id)
            await s.commit()

    async def _load(self, s: Any, tenant_id: str, approval_id: str) -> dict[str, Any] | None:
        row = (
            await s.execute(
                text(
                    "SELECT id, tenant_id, goal_id, step_description, tool_name, "
                    "       risk_level, required_approvers, status, rejection_reason, "
                    "       rejected_by, resolved_at, created_at "
                    "FROM trust_approval_requests WHERE tenant_id = :t AND id = :id"
                ),
                {"t": tenant_id, "id": approval_id},
            )
        ).fetchone()
        if row is None:
            return None
        votes = (
            await s.execute(
                text(
                    "SELECT approver_id, action, note, created_at "
                    "FROM trust_approval_votes "
                    "WHERE tenant_id = :t AND request_id = :r ORDER BY created_at ASC"
                ),
                {"t": tenant_id, "r": approval_id},
            )
        ).fetchall()
        record: dict[str, Any] = {
            "approval_id": row[0],
            "tenant_id": row[1],
            "goal_id": row[2],
            "step_description": row[3],
            "tool_name": row[4],
            "risk_level": row[5],
            "required_approvers": int(row[6]),
            "status": row[7],
            "approvers": [
                {
                    "approver_id": v[0],
                    "action": v[1],
                    "note": v[2],
                    "at": _iso(v[3]),
                }
                for v in votes
            ],
            "created_at": _iso(row[11]),
        }
        if row[8] is not None:
            record["rejection_reason"] = row[8]
        if row[9] is not None:
            record["rejected_by"] = row[9]
        if row[10] is not None:
            record["resolved_at"] = _iso(row[10])
        return record
