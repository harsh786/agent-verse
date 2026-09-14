"""Grantex — framework enforcement entry point (the mandatory tool-call gate)."""
from __future__ import annotations

from datetime import UTC, datetime, timedelta

from app.governance.grants import Grant, InMemoryGrantStore, enforce_tool_call

_NOW = datetime(2026, 1, 1, tzinfo=UTC)


def _grant() -> Grant:
    return Grant(
        grant_id="g1",
        tenant_id="t1",
        grantor="user:alice",
        grantee_agent_id="agent-1",
        scopes=("jira.*",),
        not_before=_NOW - timedelta(hours=1),
        expires_at=_NOW + timedelta(hours=1),
    )


async def test_disabled_is_passthrough() -> None:
    # Default posture: enforcement off → every call allowed, nothing regresses.
    d = await enforce_tool_call(
        None, tenant_id="t1", agent_id="a", tool_name="anything", enabled=False
    )
    assert d.allowed is True and d.reason == "enforcement_disabled"


async def test_enabled_denies_uncovered_tool() -> None:
    store = InMemoryGrantStore()
    await store.issue(_grant())
    d = await enforce_tool_call(
        store, tenant_id="t1", agent_id="agent-1", tool_name="stripe.charge", enabled=True, now=_NOW
    )
    assert d.allowed is False  # jira grant does not cover stripe


async def test_enabled_allows_covered_tool() -> None:
    store = InMemoryGrantStore()
    await store.issue(_grant())
    d = await enforce_tool_call(
        store, tenant_id="t1", agent_id="agent-1", tool_name="jira.search", enabled=True, now=_NOW
    )
    assert d.allowed is True and d.grant_id == "g1"


async def test_enabled_denies_agent_with_no_grant() -> None:
    d = await enforce_tool_call(
        InMemoryGrantStore(), tenant_id="t1", agent_id="ghost", tool_name="jira.search",
        enabled=True,
    )
    assert d.allowed is False and d.reason == "no_grant_for_agent"
