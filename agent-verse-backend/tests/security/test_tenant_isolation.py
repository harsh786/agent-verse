"""Tenant isolation regression tests.

All tests in this file verify that the per-tenant data boundary is correctly
enforced in GoalService.  Cross-tenant reads, cancellations, and list
operations must all be blocked at the service layer — independent of the
HTTP authentication middleware.

Finding: IDOR / tenant-isolation bypasses are critical-severity findings that
allow one tenant to access, cancel, or enumerate another tenant's goals.
"""
from __future__ import annotations

import asyncio

import pytest

from app.core.errors import NotFoundError
from app.services.goal_service import GoalService
from app.tenancy.context import PlanTier, TenantContext

# Stable tenant contexts reused across tests within this module.
# Using unique tenant IDs avoids cross-test state contamination.
CTX_A = TenantContext(tenant_id="iso-tenant-a", plan=PlanTier.FREE, api_key_id="key-a")
CTX_B = TenantContext(tenant_id="iso-tenant-b", plan=PlanTier.FREE, api_key_id="key-b")


def test_goal_not_visible_across_tenants():
    """Tenant B cannot read Tenant A's goals — NotFoundError must be raised."""
    svc = GoalService()

    async def run():
        result = await svc.submit_goal(
            goal="private goal",
            priority="normal",
            dry_run=True,
            tenant_ctx=CTX_A,
        )
        goal_id = result["goal_id"]

        with pytest.raises(NotFoundError):
            await svc.get_goal(goal_id=goal_id, tenant_ctx=CTX_B)

    asyncio.run(run())


def test_goal_cancel_blocked_cross_tenant():
    """Tenant B cannot cancel Tenant A's goal."""
    svc = GoalService()

    async def run():
        result = await svc.submit_goal(
            goal="secret goal",
            priority="normal",
            dry_run=True,
            tenant_ctx=CTX_A,
        )
        goal_id = result["goal_id"]

        with pytest.raises((NotFoundError, PermissionError)):
            await svc.cancel_goal(goal_id=goal_id, tenant_ctx=CTX_B)

    asyncio.run(run())


def test_list_goals_only_returns_own_tenant_goals():
    """list_goals must only return goals belonging to the requesting tenant.

    Specifically:
    - Tenant A's goals must not appear in Tenant B's list.
    - Tenant B's goal must not appear in Tenant A's list.
    - Goal IDs must be disjoint between the two tenants.
    """
    svc = GoalService()

    async def run():
        await svc.submit_goal(
            goal="goal-a1", priority="normal", dry_run=True, tenant_ctx=CTX_A
        )
        await svc.submit_goal(
            goal="goal-a2", priority="normal", dry_run=True, tenant_ctx=CTX_A
        )
        await svc.submit_goal(
            goal="goal-b1", priority="normal", dry_run=True, tenant_ctx=CTX_B
        )

        resp_a = await svc.list_goals(tenant_ctx=CTX_A)
        resp_b = await svc.list_goals(tenant_ctx=CTX_B)

        goals_a = resp_a.get("goals", [])
        goals_b = resp_b.get("goals", [])

        # Every goal in A's list must belong to tenant A (if tenant_id is present)
        for goal in goals_a:
            if "tenant_id" in goal:
                assert goal["tenant_id"] == CTX_A.tenant_id, (
                    f"Cross-tenant goal leaked into A's list: {goal}"
                )

        # Goal IDs must be disjoint — no cross-tenant leakage
        a_ids = {g["goal_id"] for g in goals_a}
        b_ids = {g["goal_id"] for g in goals_b}
        assert a_ids.isdisjoint(b_ids), (
            f"Cross-tenant goal leakage detected: "
            f"shared IDs = {a_ids & b_ids}"
        )

    asyncio.run(run())


def test_goal_count_isolated_per_tenant():
    """Each tenant must see only their own goal count in list_goals."""
    svc = GoalService()

    # Use fresh tenant IDs to avoid interference from other tests
    ctx_c = TenantContext(tenant_id="count-tenant-c", plan=PlanTier.FREE, api_key_id="kc")
    ctx_d = TenantContext(tenant_id="count-tenant-d", plan=PlanTier.FREE, api_key_id="kd")

    async def run():
        # Tenant C submits 3 goals, tenant D submits 1 goal
        for i in range(3):
            await svc.submit_goal(
                goal=f"c-goal-{i}",
                priority="normal",
                dry_run=True,
                tenant_ctx=ctx_c,
            )
        await svc.submit_goal(
            goal="d-goal-0", priority="normal", dry_run=True, tenant_ctx=ctx_d
        )

        resp_c = await svc.list_goals(tenant_ctx=ctx_c)
        resp_d = await svc.list_goals(tenant_ctx=ctx_d)

        c_count = len(resp_c.get("goals", []))
        d_count = len(resp_d.get("goals", []))

        assert c_count == 3, (
            f"Tenant C should see exactly 3 goals, got {c_count}"
        )
        assert d_count == 1, (
            f"Tenant D should see exactly 1 goal, got {d_count}"
        )

    asyncio.run(run())


def test_goal_service_get_record_enforces_tenant():
    """_get_record must raise NotFoundError for wrong-tenant access.

    This is the lowest-level enforcement point — tests the guard directly
    rather than through the public API.
    """
    svc = GoalService()

    ctx_e = TenantContext(tenant_id="e-tenant", plan=PlanTier.FREE, api_key_id="ke")
    ctx_f = TenantContext(tenant_id="f-tenant", plan=PlanTier.FREE, api_key_id="kf")

    async def run():
        result = await svc.submit_goal(
            goal="e-goal", priority="normal", dry_run=True, tenant_ctx=ctx_e
        )
        goal_id = result["goal_id"]

        # Directly test the internal _get_record guard
        with pytest.raises(NotFoundError):
            svc._get_record(goal_id, ctx_f)

        # Correct tenant: must not raise
        record = svc._get_record(goal_id, ctx_e)
        assert record.goal_id == goal_id

    asyncio.run(run())


def test_multiple_tenants_full_isolation():
    """Full isolation scenario: N tenants each with their own goals.

    No tenant must see another's goals or be able to cancel them.
    """
    from app.tenancy.context import PlanTier, TenantContext

    n = 4
    svc = GoalService()
    contexts = [
        TenantContext(
            tenant_id=f"multi-iso-tenant-{i}",
            plan=PlanTier.FREE,
            api_key_id=f"k{i}",
        )
        for i in range(n)
    ]

    async def run():
        goal_ids: dict[str, str] = {}  # tenant_id → goal_id

        # Each tenant submits one goal
        for ctx in contexts:
            result = await svc.submit_goal(
                goal=f"goal-for-{ctx.tenant_id}",
                priority="normal",
                dry_run=True,
                tenant_ctx=ctx,
            )
            goal_ids[ctx.tenant_id] = result["goal_id"]

        # Each tenant must NOT be able to access any other tenant's goal
        for owner_ctx in contexts:
            owner_goal_id = goal_ids[owner_ctx.tenant_id]
            for other_ctx in contexts:
                if other_ctx.tenant_id == owner_ctx.tenant_id:
                    continue  # own goal — should be accessible
                with pytest.raises(NotFoundError):
                    await svc.get_goal(
                        goal_id=owner_goal_id, tenant_ctx=other_ctx
                    )

        # Each tenant's own goal must still be accessible by them
        for ctx in contexts:
            goal_id = goal_ids[ctx.tenant_id]
            result = await svc.get_goal(goal_id=goal_id, tenant_ctx=ctx)
            assert result["goal_id"] == goal_id

    asyncio.run(run())
