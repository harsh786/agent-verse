"""Grantex: revocation must cascade, and max_cost_usd must actually cap spend.

Two gaps in the grant model, both of the "looks enforced but isn't" kind.

**Revocation did not reach delegated grants.** ``mint_delegation`` narrows
authority correctly at mint time and records ``parent_grant_id``, and the module
promises that "authority strictly decreases down a multi-agent chain". But
``revoke()`` set ``revoked = TRUE`` on exactly one row and ``Grant.is_active()``
consults only its own flag — so revoking a compromised supervisor's grant left
every sub-agent it had spawned holding the same scopes, right up to their own
expiry. Revocation is the half of the guarantee that matters under incident
response, and it was missing.

**max_cost_usd capped nothing.** ``Grant.covers()`` compared a *single* call's
cost against the cap, so a $10 grant authorised unbounded spend in $9.99
increments — and the only enforcement site in the product
(``executor_mixin``'s tool gate) called ``enforce_tool_call`` without a
``cost_usd`` at all, so the comparison was always ``0.0 > cap`` and the field was
dead. The cap is now a cumulative budget: spend is recorded against the grant
that authorised it and checked on every later call.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from app.governance.grants import Grant, InMemoryGrantStore, check_grant
from app.governance.grants.delegation import delegate_active_grants, mint_delegation

NOW = datetime(2026, 1, 1, 12, 0, tzinfo=UTC)


def _grant(**over: object) -> Grant:
    base: dict = {
        "grant_id": "g-root",
        "tenant_id": "t1",
        "grantor": "admin",
        "grantee_agent_id": "agent-super",
        "scopes": ("jira.*",),
        "not_before": NOW - timedelta(hours=1),
        "expires_at": NOW + timedelta(hours=10),
        "max_cost_usd": None,
    }
    base.update(over)
    return Grant(**base)  # type: ignore[arg-type]


class TestRevocationCascade:
    @pytest.mark.asyncio
    async def test_revoking_a_parent_revokes_its_delegated_children(self) -> None:
        store = InMemoryGrantStore()
        parent = await store.issue(_grant())
        children = await delegate_active_grants(
            store,
            tenant_id="t1",
            parent_agent_id="agent-super",
            child_agent_id="agent-child",
            now=NOW,
        )
        assert len(children) == 1

        before = await check_grant(
            store, tenant_id="t1", agent_id="agent-child", tool_name="jira.search", now=NOW
        )
        assert before.allowed is True

        await store.revoke("t1", parent.grant_id)

        after = await check_grant(
            store, tenant_id="t1", agent_id="agent-child", tool_name="jira.search", now=NOW
        )
        assert after.allowed is False, (
            "a sub-agent kept its parent's authority after the parent was revoked"
        )

    @pytest.mark.asyncio
    async def test_cascade_reaches_grandchildren(self) -> None:
        """Authority is delegated down arbitrary depth; revocation must follow."""
        store = InMemoryGrantStore()
        root = await store.issue(_grant())
        child = mint_delegation(
            root,
            grant_id="g-child",
            grantee_agent_id="agent-child",
            scopes=("jira.search",),
            expires_at=root.expires_at,
            now=NOW,
        )
        await store.issue(child)
        grandchild = mint_delegation(
            child,
            grant_id="g-grandchild",
            grantee_agent_id="agent-grandchild",
            scopes=("jira.search",),
            expires_at=child.expires_at,
            now=NOW,
        )
        await store.issue(grandchild)

        await store.revoke("t1", root.grant_id)

        for agent in ("agent-child", "agent-grandchild"):
            decision = await check_grant(
                store, tenant_id="t1", agent_id=agent, tool_name="jira.search", now=NOW
            )
            assert decision.allowed is False, f"{agent} survived the root revocation"

    @pytest.mark.asyncio
    async def test_revoking_a_child_leaves_the_parent_alone(self) -> None:
        """The cascade goes down the chain, never up it."""
        store = InMemoryGrantStore()
        root = await store.issue(_grant())
        child = mint_delegation(
            root,
            grant_id="g-child",
            grantee_agent_id="agent-child",
            scopes=("jira.search",),
            expires_at=root.expires_at,
            now=NOW,
        )
        await store.issue(child)

        await store.revoke("t1", "g-child")

        parent_ok = await check_grant(
            store, tenant_id="t1", agent_id="agent-super", tool_name="jira.search", now=NOW
        )
        assert parent_ok.allowed is True

    @pytest.mark.asyncio
    async def test_cannot_delegate_from_a_revoked_grant(self) -> None:
        store = InMemoryGrantStore()
        root = await store.issue(_grant())
        await store.revoke("t1", root.grant_id)
        minted = await delegate_active_grants(
            store,
            tenant_id="t1",
            parent_agent_id="agent-super",
            child_agent_id="agent-late",
            now=NOW,
        )
        assert minted == [], "a revoked grant was still able to delegate authority"


class TestCumulativeCostCap:
    @pytest.mark.asyncio
    async def test_spend_accumulates_until_the_cap_denies(self) -> None:
        store = InMemoryGrantStore()
        await store.issue(_grant(max_cost_usd=10.0))

        async def _decide(cost: float) -> bool:
            d = await check_grant(
                store,
                tenant_id="t1",
                agent_id="agent-super",
                tool_name="jira.search",
                now=NOW,
                cost_usd=cost,
            )
            return d.allowed

        # Four calls at $3 each: the first three fit inside the $10 cap, the
        # fourth would take the total to $12. Under the old per-call comparison
        # every one of them passed, forever.
        for _ in range(3):
            assert await _decide(3.0) is True
            await store.record_spend("t1", "g-root", 3.0)

        assert await _decide(3.0) is False, (
            "max_cost_usd is a per-call comparison, not a budget — $3 calls never "
            "exhaust a $10 grant"
        )

    @pytest.mark.asyncio
    async def test_exhausted_grant_denies_even_a_zero_cost_call(self) -> None:
        """The executor's tool gate cannot know a call's cost, so it passes 0.0.

        An exhausted grant must still deny there, otherwise the cap is bypassed
        by the very call site that enforces it.
        """
        store = InMemoryGrantStore()
        await store.issue(_grant(max_cost_usd=5.0))
        await store.record_spend("t1", "g-root", 5.0)

        decision = await check_grant(
            store,
            tenant_id="t1",
            agent_id="agent-super",
            tool_name="jira.search",
            now=NOW,
            cost_usd=0.0,
        )
        assert decision.allowed is False
        assert decision.reason == "cost_cap_exceeded"

    @pytest.mark.asyncio
    async def test_uncapped_grant_is_never_denied_on_cost(self) -> None:
        store = InMemoryGrantStore()
        await store.issue(_grant(max_cost_usd=None))
        await store.record_spend("t1", "g-root", 10_000.0)
        decision = await check_grant(
            store,
            tenant_id="t1",
            agent_id="agent-super",
            tool_name="jira.search",
            now=NOW,
            cost_usd=999.0,
        )
        assert decision.allowed is True

    @pytest.mark.asyncio
    async def test_delegated_child_cannot_outspend_its_own_cap(self) -> None:
        store = InMemoryGrantStore()
        root = await store.issue(_grant(max_cost_usd=100.0))
        child = mint_delegation(
            root,
            grant_id="g-child",
            grantee_agent_id="agent-child",
            scopes=("jira.search",),
            expires_at=root.expires_at,
            max_cost_usd=4.0,
            now=NOW,
        )
        await store.issue(child)
        await store.record_spend("t1", "g-child", 4.0)

        decision = await check_grant(
            store, tenant_id="t1", agent_id="agent-child", tool_name="jira.search", now=NOW
        )
        assert decision.allowed is False
        assert decision.reason == "cost_cap_exceeded"
