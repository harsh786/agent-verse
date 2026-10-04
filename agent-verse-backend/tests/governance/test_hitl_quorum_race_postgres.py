"""HITL-08: concurrent final votes on a multi-approver gate always resolve it.

``_db_cast_vote`` did INSERT vote + COUNT(*) under READ COMMITTED with no lock:
two concurrent final votes could each count below quorum, nobody ran the
compare-and-swap, and the gate expired. Votes on one request are now serialised
by a row lock on the request, so the last voter always sees the quorum.
"""

from __future__ import annotations

import asyncio
import uuid

import pytest
from sqlalchemy import text

from app.governance.hitl import HITLGateway
from app.tenancy.context import PlanTier, TenantContext


class _Rec:
    """Fake session that records SQL in order (scalar() answers the COUNT)."""

    def __init__(self, log: list[str]) -> None:
        self._log = log

    async def __aenter__(self) -> _Rec:
        return self

    async def __aexit__(self, *exc: object) -> bool:
        return False

    def begin(self) -> _Rec:
        return self

    async def execute(self, stmt: object, params: object = None) -> _Rec:
        self._log.append(" ".join(str(stmt).split()))
        return self

    def scalar(self) -> int:
        return 1


async def test_vote_locks_the_request_row_before_counting() -> None:
    log: list[str] = []
    gw = HITLGateway(db_session_factory=lambda: _Rec(log))
    await gw._db_cast_vote("r1", "t1", "alice", "")
    sql = [s for s in log if "set_config" not in s]
    assert "FOR UPDATE" in sql[0] and "approval_requests" in sql[0]
    assert sql[1].startswith("INSERT INTO approval_votes")
    assert sql[2].startswith("SELECT COUNT(*)")


@pytest.mark.integration
async def test_concurrent_final_votes_resolve_the_gate(pg_url: str) -> None:
    from tests.memory._pg import app_role_engine, sessionmaker_for

    engine = await app_role_engine(pg_url, ["approval_requests", "approval_votes"])
    try:
        factory = sessionmaker_for(engine)
        for _ in range(8):
            tenant = f"t-q-{uuid.uuid4().hex[:8]}"
            ctx = TenantContext(tenant_id=tenant, plan=PlanTier.ENTERPRISE, api_key_id="k")
            raiser = HITLGateway(db_session_factory=factory)
            rid = await raiser.request_approval_async(
                goal_id="g",
                action="deploy",
                tenant_ctx=ctx,
                required_approvers=2,
                require_persisted=True,
            )
            # Two replicas, two distinct approvers, voting at the same time.
            a, b = HITLGateway(db_session_factory=factory), HITLGateway(db_session_factory=factory)
            await asyncio.gather(
                a.approve_async(rid, approver="alice", tenant_ctx=ctx),
                b.approve_async(rid, approver="bob", tenant_ctx=ctx),
            )
            async with factory() as s, s.begin():
                await s.execute(text("SELECT set_config('app.tenant_id', :t, true)"), {"t": tenant})
                status = (
                    await s.execute(
                        text("SELECT status FROM approval_requests WHERE id = :id"), {"id": rid}
                    )
                ).scalar_one()
            assert status == "approved"
    finally:
        await engine.dispose()
