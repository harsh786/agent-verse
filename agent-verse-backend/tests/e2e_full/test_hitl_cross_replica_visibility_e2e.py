"""e2e_full: a pending HITL approval must be visible from every replica.

``HITLGateway`` keeps approvals in ``self._requests``, a per-process dict
hydrated once at startup by a fleet-wide scan. ``get_request`` and
``list_pending`` read *only* that dict — no database fallback — so in any
multi-replica deployment:

* replica B creates an approval (DB row written, B's dict updated);
* the operator's ``GET /governance/approvals`` is load-balanced to replica A,
  which started before that row existed;
* the approval is invisible on A, and ``get_request`` answers ``None`` — a 404
  for a gate that is genuinely pending and genuinely blocking a goal.

The reverse is just as bad: an approval resolved on B still reads ``pending`` on
A until A restarts.

An earlier pass fixed the approve/reject *race* (DB compare-and-swap) but not
*visibility*, which is the half an operator actually hits. A second gateway
instance over the same database stands in for the second replica here; it is
exactly what a second process constructs.
"""

from __future__ import annotations

import uuid
from typing import Any

import pytest

pytestmark = [pytest.mark.e2e_full, pytest.mark.asyncio(loop_scope="session")]


def _second_replica(app: Any) -> Any:
    """A gateway as a *different* process would construct it: same DB, empty cache."""
    from app.governance.hitl import HITLGateway

    return HITLGateway(db_session_factory=app.state.db_session_factory)


async def _tenant_ctx(tenant_client: Any) -> Any:
    from app.tenancy.context import PlanTier, TenantContext

    tenant_id = str((await tenant_client.get("/tenants/me")).json()["tenant_id"])
    return TenantContext(tenant_id=tenant_id, api_key_id="hitl", plan=PlanTier.FREE)


async def test_approval_created_on_one_replica_is_visible_on_another(
    app: Any, tenant_client: Any
) -> None:
    ctx = await _tenant_ctx(tenant_client)
    replica_a = app.state.hitl_gateway
    replica_b = _second_replica(app)

    goal_id = str(uuid.uuid4())
    request_id = await replica_b.request_approval_async(
        goal_id=goal_id,
        action="delete the production index",
        risk_level="high",
        tenant_ctx=ctx,
    )
    assert request_id

    found = await replica_a.aget_request(request_id, tenant_ctx=ctx)
    assert found is not None, (
        "an approval created on another replica is invisible here — the gateway "
        "reads only its own process-local cache"
    )
    assert found.goal_id == goal_id

    listed = await replica_a.alist_pending(tenant_ctx=ctx)
    assert any(r.request_id == request_id for r in listed), (
        f"pending listing missed a live approval from another replica: {listed}"
    )


async def test_resolution_on_one_replica_is_seen_by_another(
    app: Any, tenant_client: Any
) -> None:
    ctx = await _tenant_ctx(tenant_client)
    replica_a = app.state.hitl_gateway
    replica_b = _second_replica(app)

    request_id = await replica_b.request_approval_async(
        goal_id=str(uuid.uuid4()),
        action="rotate the signing key",
        risk_level="high",
        tenant_ctx=ctx,
    )
    # approve_async is what the API uses: it resolves the request against
    # Postgres (so it can approve a gate this replica never raised) and awaits
    # the resolution write, rather than scheduling it fire-and-forget.
    assert await replica_a.approve_async(
        request_id, approver="ops@example.com", tenant_ctx=ctx
    )

    still_pending = await replica_b.alist_pending(tenant_ctx=ctx)
    assert all(r.request_id != request_id for r in still_pending), (
        "an approval resolved on another replica still reads as pending here"
    )


async def test_pending_listing_is_tenant_scoped(app: Any, client: Any) -> None:
    from app.tenancy.context import PlanTier, TenantContext

    async def _signup() -> str:
        email = f"hitl-{uuid.uuid4().hex[:12]}@example.com"
        r = await client.post("/tenants/signup", json={"name": "HITL", "email": email})
        assert r.status_code == 201, r.text
        return str(r.json()["tenant_id"])

    tenant_a, tenant_b = await _signup(), await _signup()
    ctx_a = TenantContext(tenant_id=tenant_a, api_key_id="hitl", plan=PlanTier.FREE)
    ctx_b = TenantContext(tenant_id=tenant_b, api_key_id="hitl", plan=PlanTier.FREE)

    gateway = _second_replica(app)
    request_id = await gateway.request_approval_async(
        goal_id=str(uuid.uuid4()),
        action="tenant A's private action",
        risk_level="high",
        tenant_ctx=ctx_a,
    )

    assert await gateway.aget_request(request_id, tenant_ctx=ctx_b) is None
    assert all(r.request_id != request_id for r in await gateway.alist_pending(tenant_ctx=ctx_b))
    assert any(r.request_id == request_id for r in await gateway.alist_pending(tenant_ctx=ctx_a))


async def test_startup_does_not_hydrate_and_its_sweep_is_bounded() -> None:
    """Startup pulls no tenant's approvals into memory; its one write is bounded.

    The DB is the source of truth and every request path reads it per tenant,
    so the old fleet-wide warm-up (which also could not see any rows under the
    NOBYPASSRLS application role) is gone. What remains at startup is the
    phantom sweep on the maintenance role, capped per call.
    """
    from app.governance import hitl as hitl_mod
    from app.governance.hitl import HITLGateway

    assert not hasattr(HITLGateway, "load_pending_from_db_full")
    assert "LIMIT :lim" in hitl_mod._EXPIRE_PHANTOMS_SQL
    assert "SKIP LOCKED" in hitl_mod._EXPIRE_PHANTOMS_SQL  # concurrent replicas
    assert hitl_mod._PHANTOM_SWEEP_LIMIT <= 10_000

    class _NoRows:
        def all(self) -> list[object]:
            return []

    class _Session:
        async def execute(self, *a: object, **k: object) -> _NoRows:
            return _NoRows()

    class _Tx:
        async def __aenter__(self) -> None:
            return None

        async def __aexit__(self, *a: object) -> bool:
            return False

    class _Factory:
        def __call__(self) -> _Factory:
            return self

        async def __aenter__(self) -> _Session:
            sess = _Session()
            sess.begin = lambda: _Tx()  # type: ignore[attr-defined]
            return sess

        async def __aexit__(self, *a: object) -> bool:
            return False

    gw = HITLGateway()
    await gw.startup_restore(_Factory())
    assert gw._requests == {}
